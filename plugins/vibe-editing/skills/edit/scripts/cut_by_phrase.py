#!/usr/bin/env python3
"""cut_by_phrase.py <ep> <near_t0> <title> <music> <hook_phrase> <payoff_phrase>
HAND-CUT: locate the HOOK phrase start + PAYOFF phrase end (word-precise) in the source word
transcript near near_t0, then multicut + multifinish → 20_DELIVER. Editorial phrases chosen by hand,
so the boundaries are exactly the strong hook and the clean payoff (the method that nailed CleanYourSocials)."""
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
import sys, json, subprocess
from pathlib import Path

HERE = Path(_os.environ.get("VIBE_WORK") or ".").resolve()   # project 10_WORK dir; set VIBE_WORK or run from it
DELIVER = HERE.parent / "20_DELIVER"
CAL = Path(_acqv("content-skill-system/(1) Tik Tok/(1) Calm"))
ep, near, title, music, hook, payoff = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]
W = json.load(open(HERE / f"transcripts/{ep}_client.words.json"))["words"]
def nz(w): return "".join(c for c in w.lower() if c.isalnum())

def find_start(phrase, lo, hi):
    toks = [nz(x) for x in phrase.split() if nz(x)]
    for i, w in enumerate(W):
        if lo <= w["start"] <= hi and i+len(toks) <= len(W) and all(nz(W[i+k]["word"]) == toks[k] for k in range(len(toks))):
            return W[i]["start"]
    return None

def find_end(phrase, lo, hi):
    toks = [nz(x) for x in phrase.split() if nz(x)]
    for i in range(len(W) - len(toks) + 1):
        j = i + len(toks) - 1
        if lo <= W[j]["end"] <= hi and all(nz(W[i+k]["word"]) == toks[k] for k in range(len(toks))):
            return W[j]["end"] + 0.12
    return None

s = find_start(hook, near - 9, near + 14)
e = find_end(payoff, (s or near) + 3, (s or near) + 150)
if s is None or e is None or e <= s:
    print(f"✗ {title}: PHRASE NOT FOUND hook_start={s} payoff_end={e}"); sys.exit(1)
tt = title[0].upper() + title[1:]
cut = HERE / f"renders/cut_{title}.mp4"; out = DELIVER / f"client_DTC_POD_{tt}_Operator_20260606_V1.mp4"
print(f"{title}: [{s:.2f} → {e:.2f}] {e-s:.0f}s", flush=True)
subprocess.run([sys.executable, str(HERE/"multicut.py"), ep, f"{s}", f"{e}", str(cut), "switch"], capture_output=True, text=True)
r = subprocess.run([sys.executable, str(HERE/"multifinish.py"), ep, f"{s}", f"{e}", str(cut), str(out), "--music", str(CAL/music)], capture_output=True, text=True)
print("  " + ((r.stdout.strip().splitlines() or [""])[-1][-110:]))
import json as _j
w = _j.load(open(HERE/f"capwork/cut_{title}_norm.json"))["words"]
print(f"  OPENS: {' '.join(x['word'] for x in w[:7])}")
print(f"  ENDS:  {' '.join(x['word'] for x in w[-6:])}")
