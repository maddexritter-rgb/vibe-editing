#!/usr/bin/env python3
"""handoff_validator.py — validate the full orchestrator→sub-skill handoff payload.

Sister to `window_validator.py`. window_validator checks cut WINDOWS against the
transcript. handoff_validator checks the MANIFEST handoff to the render skill (and
through render to caption-clips, horizontal-to-vertical, mix). It catches "orchestrator
forgot to set X" and "field X has the wrong type/range" before any sub-skill runs.

Why: Anthropic's prompting playbook talk identifies orchestrator↔sub-agent communication
as the #1 failure mode in multi-agent systems. The classic shape: the orchestrator THINKS
it passed the right context; the sub-skill executes against missing/wrong context;
the failure surfaces three stages later as a broken render. This validator turns that
class of bug into a hard fail at Step 5 — BEFORE any encoding work happens.

Usage (library):
    from handoff_validator import validate_manifest
    errors, warnings = validate_manifest(manifest_path)

Usage (CLI):
    python handoff_validator.py <project_dir>
    # exit 0 = clean, exit 1 = errors, exit 2 = warnings only

Generic skill — no brand baked in.
"""
from __future__ import annotations

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
import sys
from pathlib import Path

VALID_PIPELINES = {"listicle", "qa", "single", "single_broll", "podcast", "multicam"}
VALID_PRESETS = {"talking-head", "stage", "split-top", "guest", "podcast"}
VALID_RES = {1080, "1080", "4k", "4K", 2160, "2160"}

# Brand nomenclature: BRAND_CONTENTTYPE_SOURCE_Title_Editor_YYYYMMDD_V#
VIBE_BRANDS = {"SPEAKER", "CREATOR", "CREATOR", "CLIENT"}


def _get(d, path, default=None):
    """Dotted-path getter that returns default on any missing/None segment."""
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _abs(project: Path, val) -> Path | None:
    if not val or not isinstance(val, str):
        return None
    p = Path(val)
    return p if p.is_absolute() else (project / p)


def validate_manifest(manifest_path: Path) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors block the render; warnings are advisory."""
    errors: list[str] = []
    warnings: list[str] = []

    if not manifest_path.exists():
        return ([f"manifest.json missing at {manifest_path}"], [])

    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception as e:
        return ([f"manifest.json unparseable: {e}"], [])

    project = manifest_path.parent

    # ── Top-level required fields ─────────────────────────────────────────
    title = manifest.get("title")
    if not title or not isinstance(title, str):
        errors.append("manifest.title is required (non-empty string)")

    pipeline = manifest.get("pipeline")
    if pipeline not in VALID_PIPELINES:
        errors.append(f"manifest.pipeline must be one of {sorted(VALID_PIPELINES)}, got {pipeline!r}")

    output_name = _get(manifest, "output.name")
    if not output_name:
        errors.append("manifest.output.name is required (the delivered Brand filename)")
    elif not output_name.endswith(".mp4"):
        errors.append(f"manifest.output.name must end .mp4, got {output_name!r}")
    else:
        # Soft Brand-format check (BRAND_TYPE_SOURCE_Title_Editor_YYYYMMDD_V#.mp4)
        stem = output_name[:-4]
        parts = stem.split("_")
        if len(parts) < 6:
            warnings.append(
                f"manifest.output.name doesn't match Brand nomenclature "
                f"(BRAND_TYPE_SOURCE_Title_Editor_YYYYMMDD_V#.mp4): {output_name!r}"
            )
        elif parts[0].upper() not in VIBE_BRANDS:
            warnings.append(
                f"manifest.output.name brand prefix {parts[0]!r} not in known set "
                f"{sorted(VIBE_BRANDS)} — typo?"
            )

    stages = manifest.get("stages") or {}

    # ── cut stage ─────────────────────────────────────────────────────────
    cut = stages.get("cut") or {}
    src_video = _abs(project, cut.get("source_video"))
    if not src_video:
        errors.append("manifest.stages.cut.source_video is required")
    elif not src_video.exists():
        errors.append(f"manifest.stages.cut.source_video does not exist: {src_video}")

    spec = _abs(project, cut.get("spec"))
    if not spec:
        errors.append("manifest.stages.cut.spec is required (path to cuts.json)")
    elif not spec.exists():
        errors.append(f"manifest.stages.cut.spec does not exist: {spec}")
    else:
        # cuts.json must be valid JSON with .segments[]
        try:
            cuts = json.loads(spec.read_text())
        except Exception as e:
            errors.append(f"cuts.json unparseable: {e}")
            cuts = None
        if cuts is not None:
            segs = cuts.get("segments")
            if not isinstance(segs, list):
                errors.append("cuts.json must have a 'segments' array")
            elif not segs:
                errors.append("cuts.json has no segments — clip would be empty")
            else:
                for i, s in enumerate(segs):
                    if not isinstance(s, dict):
                        errors.append(f"cuts.json segments[{i}] not an object")
                        continue
                    if "in" not in s or "out" not in s:
                        errors.append(f"cuts.json segments[{i}] missing 'in' or 'out'")
                        continue
                    if not (isinstance(s["in"], (int, float)) and isinstance(s["out"], (int, float))):
                        errors.append(f"cuts.json segments[{i}] 'in'/'out' must be numeric")
                        continue
                    if s["out"] <= s["in"]:
                        errors.append(
                            f"cuts.json segments[{i}] has out<=in ({s['out']} <= {s['in']})"
                        )
                # Total duration soft-check (Speaker format = 8–90s)
                total = sum(s["out"] - s["in"] for s in segs
                            if isinstance(s.get("in"), (int, float))
                            and isinstance(s.get("out"), (int, float)))
                if total < 8:
                    warnings.append(
                        f"cuts.json total duration {total:.1f}s < 8s — clip would be too short"
                    )
                if total > 90:
                    warnings.append(
                        f"cuts.json total duration {total:.1f}s > 90s — over the hard cap"
                    )
                # Hard-end: last segment should land on a payoff word, not a connector
                last_text = (segs[-1].get("text") or "").strip()
                if last_text and last_text[-1:] in {",", ";"}:
                    warnings.append(
                        f"last segment ends mid-clause: {last_text[-40:]!r} — hard-end rule expects "
                        f"a full sentence terminator"
                    )

    # 2-speaker pipelines need a separate audio source
    if pipeline in {"qa", "podcast", "multicam"}:
        src_audio = _abs(project, cut.get("source_audio"))
        if not src_audio:
            errors.append(
                f"manifest.stages.cut.source_audio is required for pipeline={pipeline!r} "
                "(lav / mic file for diarization + cleaner mix)"
            )
        elif not src_audio.exists():
            errors.append(f"manifest.stages.cut.source_audio does not exist: {src_audio}")

    # ── reframe stage ─────────────────────────────────────────────────────
    reframe = stages.get("reframe") or {}
    if reframe:
        preset = reframe.get("preset")
        if preset not in VALID_PRESETS:
            errors.append(
                f"manifest.stages.reframe.preset must be one of {sorted(VALID_PRESETS)}, "
                f"got {preset!r}"
            )
        zoom = reframe.get("zoom")
        if zoom is not None and (not isinstance(zoom, (int, float)) or zoom < 0.8 or zoom > 3.5):
            errors.append(
                f"manifest.stages.reframe.zoom must be a number in [0.8, 3.5], got {zoom!r}"
            )
        res = reframe.get("res")
        if res is not None and res not in VALID_RES:
            warnings.append(
                f"manifest.stages.reframe.res {res!r} not in expected set "
                f"{sorted(VALID_RES, key=str)} — typo? Reframe will likely fail."
            )
        # Face tracking is the HOUSE DEFAULT (every clip). lock_x pins X static and KILLS tracking —
        # it's a rare exception, never a default. Flag it so an accidental lock_x can't ship a dead
        # static crop unnoticed (Operator 2026-06-12). Escape: this is a WARNING, not a block.
        if reframe.get("lock_x") is True:
            warnings.append(
                "manifest.stages.reframe.lock_x=true DISABLES face tracking (X pinned static). "
                "House default is TRACKING on every clip — only lock_x a specific clip whose pan "
                "visibly looks bad AND the user asked. Confirm this clip truly needs a static X."
            )

    # ── captions stage ────────────────────────────────────────────────────
    captions = stages.get("captions") or {}
    if captions:
        ass = _abs(project, captions.get("ass"))
        no_layout = captions.get("no_layout", False)
        # Either an .ass file must exist OR no_layout signals the engine to regenerate
        if not ass and not no_layout:
            warnings.append(
                "manifest.stages.captions has neither .ass nor no_layout=true — "
                "engine will regenerate captions from scratch (may be intended)"
            )
        elif ass and not ass.exists() and not no_layout:
            warnings.append(
                f"manifest.stages.captions.ass does not exist: {ass} "
                "(engine will regenerate)"
            )

        # Speaker count vs pipeline
        speakers = _get(captions, "context.speakers")
        if pipeline in {"qa", "podcast", "multicam"} and speakers is not None and int(speakers) != 2:
            errors.append(
                f"captions.context.speakers={speakers} but pipeline={pipeline!r} expects 2 — "
                "color map will be wrong (host=white/guest=yellow won't split correctly)"
            )
        if pipeline in {"listicle", "single", "single_broll"} and speakers is not None and int(speakers) != 1:
            errors.append(
                f"captions.context.speakers={speakers} but pipeline={pipeline!r} expects 1 — "
                "single-speaker pipeline shouldn't split colors"
            )

    # ── mix stage (music) ────────────────────────────────────────────────
    mix = stages.get("mix") or {}
    if mix:
        music = mix.get("music")
        if music:
            music_path = Path(music)
            if not music_path.is_absolute():
                music_path = project / music
            if not music_path.exists():
                errors.append(f"manifest.stages.mix.music does not exist: {music}")
            else:
                # Music must come from the TikTok library (per locked rule)
                music_str = str(music_path.resolve())
                if "(1) Tik Tok" not in music_str:
                    warnings.append(
                        f"manifest.stages.mix.music is NOT in the TikTok library "
                        f"(content-skill-system/(1) Tik Tok/) — locked rule says source ONLY "
                        f"from there. Path: {music}"
                    )
                # Blacklist check
                bl = Path(_os.environ.get("VIBE_MUSIC")
                          or _acqv("content-skill-system/(1) Tik Tok")) / "MUSIC_BLACKLIST.txt"
                if bl.exists():
                    try:
                        bl_names = {ln.strip().lower() for ln in bl.read_text().splitlines()
                                    if ln.strip() and not ln.startswith("#")}
                        if music_path.name.lower() in bl_names:
                            errors.append(
                                f"manifest.stages.mix.music is BLACKLISTED: {music_path.name}"
                            )
                    except Exception:
                        pass

        for fld in ("voice_lufs", "music_lufs"):
            v = mix.get(fld)
            if v is not None and not isinstance(v, (int, float)):
                errors.append(f"manifest.stages.mix.{fld} must be numeric, got {v!r}")
        v_lufs = mix.get("voice_lufs")
        m_lufs = mix.get("music_lufs")
        if isinstance(v_lufs, (int, float)) and isinstance(m_lufs, (int, float)):
            if m_lufs > v_lufs - 8:
                warnings.append(
                    f"music ({m_lufs} LUFS) is within 8dB of voice ({v_lufs} LUFS) — "
                    "music will overpower; locked target is music ≈ voice − 10..14dB"
                )

    return errors, warnings


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project", type=Path, help="Project root containing manifest.json")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    manifest_path = a.project / "manifest.json"
    errors, warnings = validate_manifest(manifest_path)

    if not a.quiet:
        if errors:
            print("[handoff_validator] ❌ ERRORS (block render):")
            for e in errors:
                print(f"  - {e}")
        if warnings:
            print("[handoff_validator] ⚠️  WARNINGS (advisory):")
            for w in warnings:
                print(f"  - {w}")
        if not errors and not warnings:
            print(f"[handoff_validator] ✅ {manifest_path.name} clean — handoff to render is safe.")

    if errors:
        return 1
    if warnings:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
