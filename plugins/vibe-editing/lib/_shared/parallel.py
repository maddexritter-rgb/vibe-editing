#!/usr/bin/env python3
"""Brand PARALLEL-BATCH STANDARD (locked 2026-06-05; gate wired 2026-06-06).
Process clip batches concurrently instead of one-at-a-time. The M3 Max has 16 cores + 2
hardware media engines, but the pipeline historically ran clips in a serial `for` loop.
Run them in parallel.

    import sys, os; sys.path.insert(0, VIBE_SHARED)
    from parallel import run_commands
    results = run_commands(list_of_ffmpeg_arg_lists, kind="encode")   # [(returncode, stderr_tail), ...]

Worker caps (matter — too many concurrent encodes thrash the 2 media engines + disk I/O):
  - kind="encode"  (VideoToolbox jobs): cap 3  (only ~2 hardware encoders; more just queue)
  - kind="cpu"     (x264 / generic ffmpeg / whisper): min(cores-2, 8)

**MACHINE-WIDE GATE** — every encode job goes through `encode_gate.gate()`, a flock-based
semaphore in /tmp/acq_encode_slots/ that bounds TOTAL concurrent encodes across ALL Python
processes / Claude sessions on this Mac. Without it, 6 sessions each honoring "cap=3"
spawn 6×3 = 18 ffmpegs fighting over the 2 media engines. With it, the system as a whole
runs at most VIBE_ENCODE_SLOTS (default 3) regardless of how many callers. Disable with
VIBE_NO_ENCODE_GATE=1.
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
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor


def workers(kind="cpu"):
    cores = os.cpu_count() or 8
    return min(3, cores) if kind == "encode" else min(max(2, cores - 2), 8)


def _nullctx():
    from contextlib import nullcontext
    return nullcontext()


def _encode_ctx():
    """Return a context manager that holds an encode slot for the duration of one ffmpeg run.
    Falls back to a no-op if encode_gate is missing or explicitly disabled, so this module
    keeps working in older checkouts / on machines where the slot dir can't be created."""
    if os.environ.get("VIBE_NO_ENCODE_GATE") == "1":
        return _nullctx()
    try:
        from encode_gate import gate
        return gate()
    except Exception:
        return _nullctx()


def _profile_ctx(name, **fields):
    """Wrap one job in a profile.stage() if VIBE_PROFILE=1, no-op otherwise.
    Adds auto-coverage for every parallel encode without each skill having to instrument."""
    try:
        from profile import stage
        return stage(name, **fields)
    except Exception:
        return _nullctx()


def run_commands(cmds, kind="encode", max_workers=None):
    """Run a list of subprocess arg-lists (e.g. ffmpeg commands) in parallel.
    ffmpeg releases the GIL, so threads are ideal (and avoid pickling closures).
    Returns [(returncode, stderr_tail), ...] in input order. Never raises on a single failure.

    For kind="encode", each individual subprocess is wrapped in a machine-wide flock
    semaphore so total concurrent encodes (across ALL sessions on this machine) stays
    at VIBE_ENCODE_SLOTS (default 3). Submitting 30 encodes from 5 sessions still only
    runs 3 at a time on the hardware — the rest queue cheaply, no contention.
    """
    w = max_workers or workers(kind)
    out = [None] * len(cmds)
    use_gate = (kind == "encode")

    def _run(ic):
        i, cmd = ic
        # Best-effort: pick a friendly output filename for the profile entry.
        out_name = ""
        try:
            # ffmpeg output is conventionally the final positional arg.
            out_name = str(cmd[-1]) if cmd else ""
        except Exception:
            pass
        try:
            if use_gate:
                with _encode_ctx(), _profile_ctx(f"parallel_{kind}", out=out_name, idx=i):
                    p = subprocess.run(cmd, capture_output=True, text=True)
            else:
                with _profile_ctx(f"parallel_{kind}", out=out_name, idx=i):
                    p = subprocess.run(cmd, capture_output=True, text=True)
            return i, p.returncode, (p.stderr or "")[-1500:]
        except Exception as e:
            return i, 1, str(e)

    # We can let ThreadPoolExecutor oversubscribe (e.g. 30 threads) — the encode_gate
    # makes sure only N actually run ffmpeg at once, the rest block cheaply inside flock.
    # For kind="cpu" we keep the historical cap so we don't thrash unrelated workloads.
    pool_w = max(w, len(cmds)) if use_gate else w
    with ThreadPoolExecutor(max_workers=pool_w) as ex:
        for i, rc, err in ex.map(_run, enumerate(cmds)):
            out[i] = (rc, err)
    return out
