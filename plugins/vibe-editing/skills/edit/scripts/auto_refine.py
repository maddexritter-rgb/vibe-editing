#!/usr/bin/env python3
"""Auto-review + refine a batch of clips.

Workflow:
  1. For each clip in manifest, run review_clip.py
  2. Collect auto-fixes: extend_end_ms, trim_start_ms, extend_to_sentence_end
  3. Apply fixes to the candidate timestamps (writes updated manifest)
  4. Re-cut affected clips
  5. Re-reframe affected clips
  6. Optional: loop back to review until no blockers left (or max iterations)
  7. Write review.md summary

Manifest format (JSON):
  {
    "source_video": "...", "source_wav": "...", "wav_offset": 108.94,
    "transcript": "...", "silence_map": "...",
    "clips": [
      {"label": "A", "start": 311.03, "end": 340.00, "slug": "...",
       "manual_cuts": [{"start": ..., "end": ..., "match": "..."}]}
    ]
  }
"""
from __future__ import annotations
import tempfile
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
import subprocess
from pathlib import Path


EDIT = _acq("edit/scripts")
H2V  = _acq("horizontal-to-vertical/scripts")


def run_filler_detection(transcript, start, end, silence_map, out_path):
    subprocess.run(
        [sys.executable, str(EDIT / "detect_fillers.py"), str(transcript),
         "--start", str(start), "--end", str(end),
         "--silence-map", str(silence_map), "--out", str(out_path)],
        check=True, capture_output=True,
    )


def merge_manual_cuts(filler_path: Path, manual_cuts: list[dict]):
    data = json.loads(filler_path.read_text())
    data["cuts"].extend(manual_cuts)
    data["cuts"].sort(key=lambda c: c["start"])
    filler_path.write_text(json.dumps(data, indent=2))


def cut_clip(mp4, wav, offset, start, end, fillers, out):
    subprocess.run(
        [sys.executable, str(EDIT / "cut_clip.py"),
         "--mp4", mp4, "--wav", wav, "--wav-offset", str(offset),
         "--start", str(start), "--end", str(end),
         "--fillers", str(fillers),
         "--pad-lead-frames", "0", "--pad-tail-frames", "3",
         "--out", str(out)],
        check=True, capture_output=True,
    )


def reframe(input_path, output_path):
    subprocess.run(
        [sys.executable, str(H2V / "qa_reframe_v2.py"),
         str(input_path), str(output_path),
         "--preset", "talking-head", "--res", "1080"],
        check=True, capture_output=True,
    )


def find_next_sentence_end(transcript_path: Path, after_t: float,
                           max_extension: float = 5.0) -> float | None:
    """Find the nearest sentence-terminator in the original transcript after `after_t`."""
    tr = json.loads(transcript_path.read_text())
    for w in tr.get("words", []):
        if w["start"] < after_t:
            continue
        if w["start"] > after_t + max_extension:
            break
        raw = w["word"].rstrip()
        if raw.endswith((".", "!", "?")):
            return w["end"] + 0.1
    return None


def apply_auto_fixes(clip_cfg: dict, review: dict,
                     transcript_path: Path) -> tuple[dict, list[str]]:
    """Given a clip config + its review, return a (patched_config, applied_fixes_list)."""
    fixes = []
    patched = dict(clip_cfg)
    af = review.get("auto_fix", {})

    if "extend_end_ms" in af:
        extra = af["extend_end_ms"] / 1000
        patched["end"] = round(clip_cfg["end"] + extra, 3)
        fixes.append(f"extended end by {extra:.2f}s (truncated last word)")

    if "trim_start_ms" in af:
        extra = af["trim_start_ms"] / 1000
        patched["start"] = round(clip_cfg["start"] + extra, 3)
        fixes.append(f"trimmed start by {extra:.2f}s (opening fragment)")

    if af.get("extend_to_sentence_end"):
        new_end = find_next_sentence_end(transcript_path, patched["end"])
        if new_end and new_end > patched["end"]:
            fixes.append(f"extended end from {patched['end']:.2f} → {new_end:.2f} (sentence terminator)")
            patched["end"] = round(new_end, 3)

    return patched, fixes


def process(manifest: dict, h_dir: Path, v_dir: Path, review_dir: Path,
            max_iterations: int = 2):
    src_mp4 = manifest["source_video"]
    src_wav = manifest["source_wav"]
    offset = manifest["wav_offset"]
    transcript = Path(manifest["transcript"])
    silence_map = Path(manifest["silence_map"])

    for d in (h_dir, v_dir, review_dir):
        d.mkdir(parents=True, exist_ok=True)

    report_lines = ["# Clip review report", ""]
    fixes_log = {}

    for clip in manifest["clips"]:
        label = clip["label"]
        for iteration in range(max_iterations):
            start = clip["start"]
            end = clip["end"]
            slug = clip["slug"]
            manual_cuts = clip.get("manual_cuts", [])
            filler_path = Path(tempfile.gettempdir()) / "mine-work" / "fillers" / f"{label}.json"
            filler_path.parent.mkdir(parents=True, exist_ok=True)

            print(f"\n===== {label} iter {iteration+1}  {start:.2f}-{end:.2f} =====", flush=True)
            run_filler_detection(transcript, start, end, silence_map, filler_path)
            if manual_cuts:
                merge_manual_cuts(filler_path, manual_cuts)

            h_out = h_dir / f"{label}_{slug}.mp4"
            v_out = v_dir / f"{label}_{slug}.mp4"
            if h_out.exists(): h_out.unlink()
            if v_out.exists(): v_out.unlink()

            cut_clip(src_mp4, src_wav, offset, start, end, filler_path, h_out)
            print(f"  cut: {h_out.name}")
            reframe(h_out, v_out)
            print(f"  reframed: {v_out.name}")

            # Review
            review_path = review_dir / f"{label}.review.json"
            subprocess.run(
                [sys.executable, str(EDIT / "review_clip.py"), str(v_out),
                 "--transcript", str(transcript),
                 "--start", str(start), "--end", str(end),
                 "--fillers", str(filler_path),
                 "--out", str(review_path)],
                check=True, capture_output=True,
            )
            review = json.loads(review_path.read_text())
            print(f"  review: {review.get('summary')}")

            # Apply auto-fixes, update the clip config, go another iteration
            patched, fixes = apply_auto_fixes(clip, review, transcript)
            if not fixes or iteration == max_iterations - 1:
                fixes_log[label] = {"review": review, "applied": fixes}
                break
            clip.update(patched)
            print(f"  auto-fix: {', '.join(fixes)} — re-running")

    # Write summary report
    for label, entry in fixes_log.items():
        r = entry["review"]
        report_lines.append(f"## {label}")
        report_lines.append(f"_{r.get('summary','?')}_ · `{Path(r['clip']).name}`")
        if entry["applied"]:
            report_lines.append("**Auto-fixes applied:** " + "; ".join(entry["applied"]))
        if r.get("issues"):
            report_lines.append("")
            for iss in r["issues"]:
                report_lines.append(f"- **[{iss['severity']}]** {iss['type']}: {iss['msg']}")
        if r.get("needs_human"):
            report_lines.append("")
            report_lines.append("**Needs human call:**")
            for n in r["needs_human"]:
                report_lines.append(f"- {n}")
        report_lines.append("")

    report_path = v_dir / "review.md"
    report_path.write_text("\n".join(report_lines))
    print(f"\nWrote {report_path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--h-dir", type=Path,
                    default=Path.home() / "Downloads" / "clips-mined")
    ap.add_argument("--v-dir", type=Path,
                    default=Path.home() / "Downloads" / "clips-vertical")
    ap.add_argument("--review-dir", type=Path, default=Path(tempfile.gettempdir()) / "mine-work" / "reviews")
    ap.add_argument("--max-iterations", type=int, default=2)
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text())
    process(manifest, args.h_dir, args.v_dir, args.review_dir, args.max_iterations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
