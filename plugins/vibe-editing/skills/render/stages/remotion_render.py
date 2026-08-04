"""remotion_render — render a Remotion composition to the stage cache.

Used by the anthropic-demo pipeline (and any future Remotion-based recipe).

Config (manifest.stages.remotion_render):
    remotion_dir: project-relative path to the Remotion project (package.json root)
    composition:  composition id (e.g. "MainDemo")
    spec_dir:     optional project-relative dir of spec JSONs synced into src/data/
                  before render (so spec edits propagate + invalidate downstream)
    tree_digest:  project-relative path to the source-tree digest file. Regenerate it
                  before invoking the engine (scripts/render_v1.sh does this) — it's
                  what makes "edit any Remotion source → only this stage re-runs" work,
                  since the engine content-hashes config file refs, not directories.
    expected: {duration_s, tolerance_s, width, height, fps} — validated post-render

Renders via `npx remotion render` to a ProRes intermediate, then encodes the
cached mp4 through _shared/fast_encode.py encoder_args() (VideoToolbox HW H.264 —
Brand standing rule: never hand-write -c:v libx264).
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
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, VIBE_SHARED)
from fast_encode import encoder_args  # noqa: E402

VERSION = "1"


def _probe(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    j = json.loads(r.stdout or "{}")
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), {})
    num, den = (v.get("r_frame_rate") or "0/1").split("/")
    return {
        "duration_s": float(j.get("format", {}).get("duration") or 0),
        "width": v.get("width"),
        "height": v.get("height"),
        "fps": (float(num) / float(den)) if float(den) else None,
    }


def run(work_dir, config, inputs, inputs_meta, project, manifest, out_path):
    project = Path(project)
    remotion_dir = project / config["remotion_dir"]
    comp = config["composition"]

    # Sync spec JSONs into src/data so the bundle always renders current spec
    spec_dir = config.get("spec_dir")
    if spec_dir:
        for f in sorted((project / spec_dir).glob("*.json")):
            shutil.copy2(f, remotion_dir / "src" / "data" / f.name)

    with tempfile.TemporaryDirectory(prefix="remotion_render_") as td:
        intermediate = Path(td) / "intermediate.mov"
        cmd = [
            "npx", "remotion", "render", comp, str(intermediate),
            "--codec", "prores", "--prores-profile", "hq", "--log", "error",
        ]
        r = subprocess.run(cmd, cwd=remotion_dir, capture_output=True, text=True)
        if r.returncode != 0 or not intermediate.exists():
            raise RuntimeError(f"remotion render failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")

        meta = _probe(intermediate)
        exp = config.get("expected", {})
        if exp:
            tol = exp.get("tolerance_s", 2.0)
            if abs(meta["duration_s"] - exp["duration_s"]) > tol:
                raise RuntimeError(
                    f"duration {meta['duration_s']:.2f}s outside {exp['duration_s']}±{tol}s")
            if (meta["width"], meta["height"]) != (exp["width"], exp["height"]):
                raise RuntimeError(
                    f"resolution {meta['width']}x{meta['height']} != {exp['width']}x{exp['height']}")

        # HW encode to the stage cache (VideoToolbox via fast_encode)
        enc = ["ffmpeg", "-y", "-i", str(intermediate),
               *encoder_args(meta["width"], meta["height"], "ffmpeg", tier="delivery"),
               "-an", str(out_path)]
        r2 = subprocess.run(enc, capture_output=True, text=True)
        if r2.returncode != 0:
            raise RuntimeError(f"fast_encode pass failed:\n{r2.stderr[-2000:]}")

    return {"out": str(out_path), "meta": meta}
