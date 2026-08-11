#!/usr/bin/env python3
"""Cut ALL approved candidates in PARALLEL (vs the old one-at-a-time loop).

Reads candidates.json, builds a cut_clip.py command per candidate, runs them concurrently
(capped for the hardware encoders via the Brand parallel standard). ~Nx faster on a batch.

    python3 batch_cut.py --candidates candidates.json --mp4 in.mp4 --wav in.wav \
        --wav-offset 0 --fillers-dir fillers --out-dir clips [--lut luts/x.cube] [--master]

Each candidate dict needs: label, slug, start_wav, end_wav. Fillers are read from
<fillers-dir>/<label>.json if present (the merged fillers+llm_edit cut-list).
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
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, VIBE_SHARED)
from parallel import run_commands  # Brand parallel-batch standard

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=Path, required=True)
    ap.add_argument("--mp4", type=Path, required=True)
    ap.add_argument("--wav", type=Path, required=True)
    ap.add_argument("--wav-offset", type=float, default=0.0)
    ap.add_argument("--fillers-dir", type=Path, default=Path("fillers"))
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--lut", type=Path, default=None)
    ap.add_argument("--master", action="store_true")
    a = ap.parse_args()

    cands = json.loads(a.candidates.read_text())["candidates"]
    a.out_dir.mkdir(parents=True, exist_ok=True)

    cmds, names = [], []
    for c in cands:
        name = f"{c['label']}_{c['slug']}"
        cmd = [sys.executable, str(HERE / "cut_clip.py"),
               "--mp4", str(a.mp4), "--wav", str(a.wav), "--wav-offset", str(a.wav_offset),
               "--start", str(c["start_wav"]), "--end", str(c["end_wav"]),
               "--out", str(a.out_dir / f"{name}.mp4")]
        fj = a.fillers_dir / f"{c['label']}.json"
        if fj.exists():
            cmd += ["--fillers", str(fj)]
        if a.lut:
            cmd += ["--lut", str(a.lut)]
        if a.master:
            cmd += ["--master"]
        cmds.append(cmd)
        names.append(name)

    print(f"Cutting {len(cmds)} clips in parallel (VideoToolbox, capped for the media engines)…", flush=True)
    res = run_commands(cmds, kind="encode")
    ok = 0
    for (rc, err), name in zip(res, names):
        if rc == 0:
            ok += 1
        else:
            print(f"  ✗ {name}: {err[-200:]}")
    print(f"done: {ok}/{len(cmds)} cut OK → {a.out_dir}")
    return 0 if ok == len(cmds) else 1


if __name__ == "__main__":
    raise SystemExit(main())
