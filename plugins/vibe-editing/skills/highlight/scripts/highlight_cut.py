#!/usr/bin/env python3
"""
highlight_cut.py — cut a chosen mid candidate into a finished 16:9 video.

Reuses the shared `lib/_shared/precision_cut.py` (true-acoustic-end cut + per-speaker dead-air
trim + canon loudnorm). Mids stay HORIZONTAL — there is NO reframe. The cut itself IS the
render for a mid; optional light captions are an explicit hook (off by default).

Inputs:
  --src    the source recording (16:9)
  --words  word-level transcript JSON: {"words":[{"word","start","end"}, ...]}
  --keep   keep spans by WORD INDEX from the HOOK->MEAT->PAYOFF CLIPPER stage, e.g. [[0,40],[55,80]]
  --out    finished mid .mp4

CTA outro is OPTIONAL and user-supplied: if brand/cta/outro.mp4 (repo root) exists it is
appended; if not, it is SKIPPED gracefully (never a hard-fail). Override with --cta <path>,
or force-skip with --no-cta.

Adds over raw precision_cut: auto silence-floor (mean_volume - 8dB), 16:9 sanity check, and a
clip.contract.json (declared vs observed) the audit agents read.
"""
# ── vibe-editing portable path bootstrap ──
import os as _os, sys as _sys
def _vibe_root():
    r = _os.environ.get("VIBE_PIPELINE_ROOT") or _os.environ.get("CLAUDE_PLUGIN_ROOT")
    if r and _os.path.isdir(_os.path.join(r, ".claude-plugin")):
        return r
    d = _os.path.dirname(_os.path.abspath(__file__))
    while d != _os.path.dirname(d):
        if _os.path.isdir(_os.path.join(d, ".claude-plugin")):
            return d
        d = _os.path.dirname(d)
    return _os.path.dirname(_os.path.abspath(__file__))
VIBE_ROOT = _vibe_root()
VIBE_SHARED = _os.path.join(VIBE_ROOT, "lib", "_shared")
# ── end bootstrap ──
import argparse, json, re, subprocess, sys

PRECISION_CUT = _os.path.join(VIBE_SHARED, "precision_cut.py")
HERE = _os.path.dirname(_os.path.abspath(__file__))
CTA_SCRIPT = _os.path.join(HERE, "highlight_cta.py")
FFMPEG, FFPROBE = "ffmpeg", "ffprobe"


def probe(path):
    out = subprocess.check_output([FFPROBE, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-show_entries", "format=duration",
        "-of", "json", path]).decode()
    j = json.loads(out); st = j["streams"][0]
    return int(st["width"]), int(st["height"]), float(j["format"]["duration"])


def measure_floor(path, margin=8.0):
    """Per-source silence floor ≈ mean_volume - margin dB (don't guess — measure)."""
    err = subprocess.run([FFMPEG, "-hide_banner", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    m = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", err)
    if not m:
        return "-40dB"
    return f"{float(m.group(1)) - margin:.0f}dB"


def cta_path(explicit):
    """Resolve the optional user-supplied outro (brand/cta/outro.mp4 at repo root)."""
    if explicit:
        return explicit
    env = _os.environ.get("VIBE_BRAND")
    cands = []
    if env:
        cands.append(_os.path.join(env, "cta", "outro.mp4"))
    cands.append(_os.path.join(_os.path.dirname(_os.path.dirname(VIBE_ROOT)), "brand", "cta", "outro.mp4"))
    cands.append(_os.path.join(VIBE_ROOT, "brand", "cta", "outro.mp4"))
    for c in cands:
        if _os.path.exists(c):
            return c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--words", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--keep", help="JSON keep spans by word index, e.g. [[0,40],[55,80]]")
    g.add_argument("--keep-file")
    ap.add_argument("--out", required=True)
    ap.add_argument("--floor", default="auto", help="silence floor; 'auto' = mean_volume - 8dB")
    ap.add_argument("--max-pause", default="0.30", help="mids run relaxed (0.30) vs shorts (0.15)")
    ap.add_argument("--captions", action="store_true", help="burn light captions (optional; off by default)")
    ap.add_argument("--cta", default=None, help="outro clip to append (default: brand/cta/outro.mp4 if present)")
    ap.add_argument("--no-cta", action="store_true", help="never append a CTA outro, even if one exists")
    ap.add_argument("--match-loudness", action="store_true", help="loudnorm the outro to -16 LUFS to match the mid")
    ap.add_argument("--cta-xfade", type=float, default=0.0, help="crossfade secs into CTA (0=hard cut)")
    a = ap.parse_args()

    if not _os.path.exists(PRECISION_CUT):
        sys.exit(f"missing engine: {PRECISION_CUT}")
    keep = json.loads(a.keep) if a.keep else json.load(open(a.keep_file))
    w, h, srcdur = probe(a.src)
    if w / h < 1.2:
        print(f"⚠️  source looks vertical ({w}x{h}); /highlight mids are 16:9 — check the input.")
    floor = measure_floor(a.src) if a.floor == "auto" else a.floor
    print(f"[cut] src {w}x{h} {srcdur:.1f}s | floor {floor} | {len(keep)} keep-span(s)")

    cmd = [sys.executable, PRECISION_CUT, "--src", a.src, "--transcript", a.words,
           "--keep", json.dumps(keep), "--floor", floor, "--max-pause", a.max_pause, "--out", a.out]
    if subprocess.run(cmd).returncode or not _os.path.exists(a.out):
        sys.exit("precision_cut failed")

    if a.captions:
        print("[cut] --captions set: integration hook for caption-clips (16:9). Mids usually ship without; wire when wanted.")

    # OPTIONAL CTA outro — appended only if the user supplied one (brand/cta/outro.mp4).
    cta_appended, cta_s = False, 0.0
    cta = None if a.no_cta else cta_path(a.cta)
    if a.no_cta:
        print("[cut] --no-cta: not appending an outro.")
    elif not cta:
        print("[cut] no outro found (brand/cta/outro.mp4) — skipping CTA (optional). Mid delivered without it.")
    else:
        nocta = a.out.rsplit(".", 1)[0] + "_nocta.mp4"
        _os.replace(a.out, nocta)
        cmd = [sys.executable, CTA_SCRIPT, "--mid", nocta, "--out", a.out, "--cta", cta, "--xfade", str(a.cta_xfade)]
        if a.match_loudness:
            cmd.append("--match-loudness")
        r = subprocess.run(cmd)
        if r.returncode or not _os.path.exists(a.out):
            # CTA is optional: a failed append should not lose the mid. Restore the no-CTA cut.
            _os.replace(nocta, a.out)
            print("[cut] ⚠️ CTA append failed — delivering the mid WITHOUT the outro (CTA is optional).")
        else:
            _os.remove(nocta); cta_appended = True; cta_s = probe(cta)[2]

    ow, oh, od = probe(a.out)
    W = json.load(open(a.words)); W = W["words"] if isinstance(W, dict) else W
    declared = sum(W[b]["end"] - W[s]["start"] for s, b, *_ in keep)
    contract = {
        "src": a.src, "out": a.out,
        "declared_keep_s": round(declared, 2), "observed_out_s": round(od, 2),
        "out_dims": f"{ow}x{oh}", "is_16_9": abs(ow / oh - 16 / 9) < 0.06,
        "captions": bool(a.captions), "cta_appended": cta_appended, "cta_s": round(cta_s, 1),
    }
    cj = a.out.rsplit(".", 1)[0] + ".contract.json"
    json.dump(contract, open(cj, "w"), indent=2)
    print(f"[cut] ✅ {a.out}  {ow}x{oh}  {od:.1f}s (declared ~{declared:.1f}s)  contract -> {cj}")


if __name__ == "__main__":
    main()
