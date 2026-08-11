"""captions — burn the LOCKED spice captions onto the upstream video.

Routes through the caption-clips SINGLE-SOURCE engine `spice_caption.py`
(= transcribe -> spice chain -> generate_spice --burn: the two-layer gblur Premiere
drop shadow, resolution-adaptive scaling, per-word white/yellow color). This REPLACES
the old plain ffmpeg `subtitles=` burn of a static .ass, which shipped the shadow-less,
wrong-style captions in /edit. A caption_qc gate inside spice_caption ABORTS the render
if the output isn't the locked spice version, so the wrong version can never ship again.

Config (all optional):
    {
      "context": "<director hint, e.g. a Q&A speaker-color note>",
      "preset":  "<path to spice.json|spice_1080.json>",  # default: auto-picked by frame height
      "corrections": {"heard": "burned"},                 # per-clip word-text fixes applied to the
                                                          # transcription (e.g. "quadruple"->"quadrupled")
      "split_seam": true                                  # (auto) ride the seam when reframe has a split;
    }                                                     # set false ONLY to override the auto seam rule
Legacy manifests with {"ass": "10_WORK/captions.ass"} are honored for backward-compat by
IGNORING that key and regenerating correct spice captions from the clip's own audio — so an
old project can't resurrect the wrong static-.ass burn.

SPLIT-SCREEN AUTO-SEAM (locked 2026-06-12): if the reframe stage has a `split` (any stacked
two-shot segment), captions MUST ride the SEAM, not the chest line — this is the caption-clips
house rule (`caption-clips/SKILL.md`: "caption sits in the seam on split-screen; below the chin
on a single cam"; `spice_qa_locked_recipe.md`: "Split-screen: caption rides the SEAM between
the two panels"). make_splitscreen always stacks 50/50, so the seam is exactly 50% = the
`spice.json` default. This stage now FORCES the seam preset automatically whenever a split is
present, so a project left on a chest-line preset (e.g. spice_speaker58 @58%) can no longer ship a
split clip with mis-placed captions. (It was a manual step and got missed — now it's code.)
"""
from __future__ import annotations
import sys

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
    import pathlib as _pl
    parts = [x for x in str(p).strip("/").split("/") if x]
    if parts and parts[0] == "_shared":
        return _pl.Path(_os.path.join(VIBE_ROOT, "lib", *parts))
    return _pl.Path(_os.path.join(VIBE_SKILLS, *parts))
def _acqv(p):
    import pathlib as _pl
    return _pl.Path(_os.path.join(VIBE_VAULT, *[x for x in str(p).strip("/").split("/") if x]))
if VIBE_SHARED not in _sys.path:
    _sys.path.insert(0, VIBE_SHARED)
# ── end bootstrap ──
import hashlib
import subprocess
from pathlib import Path

SPICE_CAPTION = _acq("caption-clips/scripts/spice_caption.py")
SEAM_PRESET = _acq("caption-clips/presets/spice.json")  # y=50% = the 50/50 split seam

# CACHE-CORRECTNESS (2026-06-12): the engine's cache key includes this stage's VERSION but NOT the
# external caption scripts the stage shells out to. So editing spice_format.py (the "no one"->"no 1"
# fix) left every caption cache VALID — the engine silently served the STALE pre-fix caption. For an
# autonomous workflow that's fatal: fix a script, nothing re-renders, broken output ships. Fix: fold a
# content-hash of the caption scripts into VERSION, so any edit to them invalidates all caption caches.
_DEPS = [
    SPICE_CAPTION,
    _acq("caption-clips/scripts/spice_format.py"),
    _acq("caption-clips/scripts/generate_spice.py"),
    _acq("caption-clips/scripts/caption_director.py"),
]

def _dep_hash() -> str:
    h = hashlib.sha256()
    for p in _DEPS:
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"missing")
    return h.hexdigest()[:8]

VERSION = "2.4.0+" + _dep_hash()  # 2.4.0: GEN (audio-only, cached) / BURN (pixels) split — pixel-only
                                  # revisions reuse the styled caption file, skipping the ~40-80s director.


def run(work_dir, config, inputs, inputs_meta, project, manifest, out_path):
    if not SPICE_CAPTION.exists():
        raise FileNotFoundError(f"spice_caption engine missing: {SPICE_CAPTION}")
    prior = inputs[list(inputs.keys())[-1]]   # grade output — the VIDEO to burn onto (pixel-dependent)
    audio_src = inputs.get("cut") or prior    # most-upstream STABLE audio for caption GENERATION

    cap_work = Path(work_dir) / "captions_work"
    cap_work.mkdir(parents=True, exist_ok=True)

    # Resolve the GEN inputs (context / corrections / pinned words) — these drive the styled caption file.
    ctx = config.get("context")
    ctx = str(ctx) if (ctx and not str(ctx).lstrip().lower().startswith("host=")) else None
    words_path = None
    if config.get("words"):
        wp = Path(config["words"])
        words_path = wp if wp.is_absolute() else (Path(project) / wp)
    corr_path = None
    if config.get("corrections"):
        import json as _json
        corr_path = cap_work / "corrections.json"
        corr_path.write_text(_json.dumps(config["corrections"]))

    # ── GEN-CACHE KEY — PIXEL-INDEPENDENT (the win Operator asked for, 2026-06-12) ──
    # The styled caption file (transcript → spice_format → LLM director) depends ONLY on the audio/words,
    # the director context, corrections, and the caption scripts' versions — NOT on reframe/grade/zoom/
    # split/preset/layout (those only change the BURN). So we key the expensive GEN on exactly those, and
    # reuse it across every pixel-only re-render: no ~40-80s director re-roll, and byte-identical captions
    # (kills the run-to-run director drift). `cut_id` = the cut stage's cache_key (its output filename),
    # which is stable when reframe changes (cut cache-hits) and changes when the CUT changes (→ regen, correct).
    import hashlib as _hl, json as _json2
    def _fh(p): return _hl.sha256(Path(p).read_bytes()).hexdigest()[:12]
    cut_id = Path(audio_src).stem
    genkey = _hl.sha256(_json2.dumps({
        "cut": cut_id,
        "ctx": ctx or "default",
        "corr": _fh(corr_path) if corr_path else "none",
        "words": _fh(words_path) if (words_path and words_path.exists()) else "none",
        "deps": _dep_hash(),
    }, sort_keys=True).encode()).hexdigest()[:16]
    gendir = Path(work_dir) / "caption_gen_cache" / genkey
    gen_ready = (gendir / "spice_norm.json").exists() and (gendir / "director_stream.json").exists()

    # ── GEN (cached): transcribe → spice_format → director. Once per genkey, reused on pixel-only re-renders.
    if gen_ready:
        print(f"  ↳ caption-gen HIT ({genkey}) — reusing styled captions (transcribe+director skipped)", flush=True)
    else:
        print(f"  ↳ caption-gen MISS ({genkey}) — generating styled captions from {Path(audio_src).name}", flush=True)
        gen_cmd = [sys.executable, str(SPICE_CAPTION), str(audio_src), "--gen-only", "--work", str(gendir)]
        if ctx: gen_cmd += ["--context", ctx]
        if corr_path: gen_cmd += ["--corrections", str(corr_path)]
        if words_path: gen_cmd += ["--words", str(words_path)]
        r = subprocess.run(gen_cmd)
        if r.returncode != 0:
            raise SystemExit(f"captions stage FAILED (caption-gen exit {r.returncode}) — render aborted")

    # ── SPLIT-SCREEN CAPTION HEIGHT (HOUSE RULE, auto-enforced 2026-06-12) ──
    # caption-clips/SKILL.md: "caption sits in the SEAM on split-screen; below the chin on a single cam."
    # make_splitscreen stacks 50/50, so the seam = exactly 50%. Two cases:
    #   • MIXED clip (some split segments + some close-up/single-cam segments — e.g. a close-up intro
    #     then a split payoff): build a PER-SECTION layout so split panels ride the seam (50%) AND the
    #     close-up segments sit below the chin (closeup_y_pct, default 58%). A single blanket Y can't do
    #     both. The Y snaps on the camera cut between sections (generate_spice frame-exact transition).
    #   • ALL-split clip: blanket static seam (no close-up to place lower).
    # Escape hatch: captions.split_seam=false.
    preset = config.get("preset")
    no_layout = bool(config.get("no_layout"))
    layout_file = None
    reframe_cfg = ((manifest or {}).get("stages") or {}).get("reframe") or {}
    split_segments = ((reframe_cfg.get("split") or {}).get("segments")) or []
    split_set = set(split_segments)
    cut_meta = inputs_meta.get("cut", {}) if inputs_meta else {}
    segs = cut_meta.get("segments") or []
    fps = cut_meta.get("fps") or 30.0
    mixed = bool(split_set) and segs and 0 < len(split_set) < len(segs)

    if split_set and config.get("split_seam") is not False:
        if mixed:
            closeup_y = float(config.get("closeup_y_pct", 0.58))  # below the chin; this clip may go a bit lower
            import json as _json
            lay = {"meta": {"fps": fps}, "segments": []}
            for i, s in enumerate(segs):
                lay["segments"].append({
                    "start_i": int(s["in_frame"]),
                    "end_i": int(s["out_frame"]) - 1,
                    "safe_y_pct": 0.50 if i in split_set else closeup_y,  # split→seam, else→below-chin
                })
            layout_file = cap_work / "auto_layout.json"
            layout_file.write_text(_json.dumps(lay))
            preset = preset or str(SEAM_PRESET)
            no_layout = False  # the layout drives per-section Y
            print(f"  ↳ split+close-up mix → per-section caption Y (split→seam 50%, "
                  f"close-up→{closeup_y*100:.0f}%), snapping on the cut", flush=True)
        else:
            if preset and Path(preset).name != SEAM_PRESET.name:
                print(f"  ↳ split-screen → captions forced to the SEAM (spice.json @50%), "
                      f"overriding preset '{Path(preset).name}'", flush=True)
            else:
                print("  ↳ split-screen → captions ride the SEAM (spice.json @50%)", flush=True)
            preset = str(SEAM_PRESET)
            no_layout = True

    # ── BURN (pixel-dependent): layout on the framed video + generate_spice --burn, reusing gendir. ──
    burn_cmd = [sys.executable, str(SPICE_CAPTION), str(prior), str(out_path),
                "--burn-from", str(gendir), "--work", str(cap_work)]
    if preset:
        burn_cmd += ["--preset", str(preset)]
    if layout_file:
        burn_cmd += ["--layout-file", str(layout_file)]
    elif no_layout:
        burn_cmd += ["--no-layout"]
    r = subprocess.run(burn_cmd)
    if r.returncode != 0:
        # Non-zero propagates: the render engine aborts the whole render — nothing ships.
        raise SystemExit(f"captions stage FAILED (caption-burn exit {r.returncode}) — render aborted")

    upstream_meta = inputs_meta.get(list(inputs_meta.keys())[-1], {}) if inputs_meta else {}
    return {"out": str(out_path), "meta": {
        "engine": "spice_caption (generate_spice --burn, two-layer gblur shadow)",
        "fps": upstream_meta.get("fps"),
        "total_duration_s": upstream_meta.get("total_duration_s"),
    }}
