#!/usr/bin/env python3
"""pick_music.py — pick vibe-matched beds from the music index, CALIBRATED to the user's approved
tracks. Ranks candidates by similarity (energy, brightness, onset-density, clamped tempo) to the
centroid of _APPROVED.txt, excludes _BLACKLIST.txt + --used + already-approved (no repeats), and
stays inside the indexed folder (TikTok-only by default). The user's EAR confirms the top pick; add
winners to _APPROVED.txt so the lane sharpens. Brand-agnostic / reusable. Build the index first with
music_index.py.

Usage: pick_music.py [--used "sub1|sub2|..."] [--n 6] [--folder "(1) Calm"]
                     [--index P] [--approved P] [--blacklist P]
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
import json, argparse, math, re
from pathlib import Path

ROOT = Path(_os.environ.get("VIBE_MUSIC") or _acqv("content-skill-system/(1) Tik Tok"))
FEATS = [("energy", 0.06), ("brightness", 500), ("onset_density", 0.7), ("tempo_c", 30)]


def load_list(p):
    p = Path(p)
    if not p.exists():
        return []
    return [re.split(r"[|\t]", l)[0].strip() for l in p.read_text().splitlines()
            if l.strip() and not l.strip().startswith("#")]


def clamp_t(t):
    return t / 2 if t and t > 120 else (t or 92)        # halve obvious double-time (ambient/rubato noise)


def vec(v):
    return {"energy": v.get("energy", 0.1), "brightness": v.get("brightness", 700),
            "onset_density": v.get("onset_density", 1.0), "tempo_c": clamp_t(v.get("tempo"))}


def dist(a, b):
    return math.sqrt(sum(((a[f] - b[f]) / s) ** 2 for f, s in FEATS))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default=str(ROOT / "_music_index.json"))
    ap.add_argument("--approved", default=str(ROOT / "_APPROVED.txt"))
    ap.add_argument("--blacklist", default=str(ROOT / "MUSIC_BLACKLIST.txt"))
    ap.add_argument("--used", default="", help="pipe-separated substrings already used this batch")
    ap.add_argument("--folder", default="(1) Calm", help="restrict to a sub-folder ('' = all)")
    ap.add_argument("--n", type=int, default=6)
    a = ap.parse_args()
    idx = json.load(open(a.index))
    approved = load_list(a.approved)
    banned = load_list(a.blacklist)
    used = [u.strip().lower() for u in a.used.split("|") if u.strip()]

    appv = [vec(v) for k, v in idx.items()
            if any(x.lower() in k.lower() for x in approved) and "error" not in v]
    cen = ({f: sum(x[f] for x in appv) / len(appv) for f, _ in FEATS} if appv
           else {"energy": 0.12, "brightness": 660, "onset_density": 0.85, "tempo_c": 92})

    def excluded(k, v):
        kl = k.lower()
        if "error" in v or any(b.lower() in kl for b in banned):
            return True
        if any(u in kl for u in used):
            return True
        if any(x.lower() in kl for x in approved):                # don't re-pick an already-placed approved track
            return True
        if a.folder and v.get("folder") != a.folder:
            return True
        return False

    cands = sorted(((dist(vec(v), cen), k, v) for k, v in idx.items() if not excluded(k, v)),
                   key=lambda x: x[0])
    print(f"centroid from {len(appv)} approved track(s): "
          f"{', '.join(f'{f}={cen[f]:.2f}' for f, _ in FEATS)}\n")
    for d, k, v in cands[:a.n]:
        print(f"  d={d:.2f} | {v.get('vibe', '?'):15} t={clamp_t(v.get('tempo')):5.0f} "
              f"e={v.get('energy', 0):.3f} bright={v.get('brightness', 0):5.0f} "
              f"{v.get('mode', '?'):5} | {k[:54]}")


if __name__ == "__main__":
    main()
