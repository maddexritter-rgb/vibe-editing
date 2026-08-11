"""broll — cut away to B-roll video while the speaker's audio continues underneath.

Built 2026-08-03 (Windows port, QEST4): the CLIP_CUTTING_PLAYBOOK documents the b-roll rules
but the original `broll-cutaway/` tool did not ship in the starter kit. This stage implements
those rules:
  - opt-in, "less is more" — no items configured -> pure passthrough
  - cut to VIDEO, never photos; ~2s shots
  - CENTER the b-roll subject (scale-to-fill + center crop to the main frame)
  - NO transitions, NO SFX — hard cuts; the speaker's audio runs untouched
  - sits BEFORE captions in the pipeline so captions burn LAST, over the b-roll

Config (manifest.stages.broll):
    {
      "items": [
        {"src": "C:/abs/path/broll.mp4",   # b-roll VIDEO file (its audio is discarded)
         "at": 10.3,                        # clip-time seconds where the cutaway starts
         "dur": 2.0,                        # cutaway length (~2s per the playbook)
         "src_in": 0.0}                     # optional: seconds into the b-roll file to start
      ]
    }
"""
from __future__ import annotations

import shutil
import subprocess
from _util import enc_v, run as ff, ffprobe_fps, resolve_path

VERSION = "1.0.0"


def _probe_wh(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True, check=True)
    raw = out.stdout.strip().splitlines()[0].strip().strip(",")
    w, h = raw.split(",")[:2]
    return int(w), int(h)


def run(work_dir, config, inputs, inputs_meta, project, manifest, out_path):
    prior = inputs[list(inputs.keys())[-1]]  # last upstream output
    items = config.get("items") or []
    upstream_meta = inputs_meta.get(list(inputs_meta.keys())[-1], {}) if inputs_meta else {}

    if not items:  # opt-in: nothing configured -> passthrough
        shutil.copyfile(prior, out_path)
        return {"out": str(out_path), "meta": {**upstream_meta, "broll_items": 0}}

    w, h = _probe_wh(prior)
    fps = ffprobe_fps(prior)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(prior)]
    for it in items:
        cmd += ["-ss", str(float(it.get("src_in", 0.0))), "-i", str(resolve_path(it["src"], project))]

    parts, cur = [], "0:v"
    for n, it in enumerate(items, start=1):
        at, dur = float(it["at"]), float(it.get("dur", 2.0))
        parts.append(
            f"[{n}:v]fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},trim=duration={dur},setpts=PTS-STARTPTS+{at}/TB[b{n}]"
        )
        parts.append(
            f"[{cur}][b{n}]overlay=eof_action=pass:enable='between(t,{at},{at + dur})'[v{n}]"
        )
        cur = f"v{n}"
    fc = ";".join(parts)

    ff(cmd + ["-filter_complex", fc, "-map", f"[{cur}]", "-map", "0:a",
              *enc_v("20M"), "-c:a", "copy", "-movflags", "+faststart", str(out_path)])

    return {"out": str(out_path), "meta": {
        "fps": upstream_meta.get("fps") or fps,
        "total_duration_s": upstream_meta.get("total_duration_s"),
        "segments": upstream_meta.get("segments"),
        "broll_items": len(items),
        "broll": [{"src": str(i["src"]), "at": i["at"], "dur": i.get("dur", 2.0)} for i in items],
    }}
