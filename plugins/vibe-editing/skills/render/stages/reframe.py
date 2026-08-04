"""reframe — apply canonical face-tracking (qa_reframe_v2.py) to the WHOLE cut in ONE pass.

LOCKED 2026-06-11: single-pass whole-clip reframe (was: per-segment reframe + concat).
The horizontal-to-vertical skill is explicit that per-segment-split-then-concat is the
DEPRECATED approach — it causes a visible framing wobble at every seam (each segment gets
its own independent Y-lock + X-center, so the crop jumps between cuts on a single continuous
angle). The locked architecture is ONE qa_reframe_v2 call over the whole concatenated cut:
one continuous Y-lock + smoothed X track = stable framing, no seam wobble. qa_reframe_v2
internally does single-pass scene-aware tracking (auto scene-split for true camera cuts; a
silence-removal seam on the same angle is correctly treated as continuous).

SPLIT-SCREEN SEGMENTS (2.4.0, 2026-06-12): a cut segment whose source shot is a WIDE
TWO-SHOT can be rendered as a stacked split-screen (subject A top / subject B bottom,
h2v make_splitscreen.py seam shadow) instead of a single-face crop. The full single-pass
reframe still runs for the whole clip; each split segment is then rebuilt from the cut
output as two ROI-restricted qa_reframe_v2 tiles + make_splitscreen, and spliced into the
full-pass video FRAME-EXACTLY at the segment boundaries. AUDIO IS NEVER CUT — the final
assembly maps the full-pass audio stream through untouched (-c:a copy), so split segments
add zero audio-seam risk.

Config (manifest.stages.reframe):
    {
      "preset": "talking-head",     # qa_reframe_v2 preset (locked house template)
      "zoom":   1.6,                 # optional — overrides the preset's zoom
      "res":    "4k",                # "1080" or "4k"
      "eye_y":  null,                # optional eyeline override
      "scene_split": null,           # true forces --scene-split, false forces --no-scene-split
      "split": {                     # OPTIONAL split-screen segments (wide two-shot -> stacked)
                                     # cuts.json AUTHORING RULE when a split segment starts ON a
                                     # source camera cut: set the split segment's "in" AT the cut
                                     # frame's pts and the PREVIOUS segment's "out" a few ms BELOW
                                     # it (in the gap after the last old-angle frame) — an out
                                     # exactly on the cut pts duplicates the cut frame into the
                                     # previous segment's tail (float -t fuzz), and that stray
                                     # frame renders with the WRONG tracking crop for one frame.
        "segments": [2],             # cut-segment indices (cuts.json order) to render split
        "top":    {"preset": "guest", "roi": [0.08,0.06,0.40,0.45], "zoom": 1.5, "eye_y": 0.25},
        "bottom": {"preset": "guest", "roi": [0.60,0.06,0.95,0.45], "zoom": 1.5, "eye_y": 0.25},
        # ZOOM on a wide two-shot: ~1.5 = natural shoulders-up medium per tile. Do NOT crank to
        # 2.0+ — on a far two-shot each face is already small, so a high zoom hard-crops to a
        # blown-up head and soft-upscales (Operator 2026-06-12: "way too zoomed"). The ROI selects
        # WHICH subject; zoom only tightens. Set ROI y-floor ~0.06 (faces sit ~0.20 in a wide).
        "crop_y": 192,               # tile crop offset px (output scale; eyes ~40% of tile)
        "shadow_strength": 1.0,      # make_splitscreen seam shadow
        "detw": 2560                 # detection width for tiles (wide shots = tiny faces)
      }
    }
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
import re
from pathlib import Path

from _util import enc_v, run as ff, resolve_path

QA_REFRAME = _acq("horizontal-to-vertical/scripts/qa_reframe_v2.py")
MAKE_SPLIT = _acq("horizontal-to-vertical/scripts/make_splitscreen.py")

# FACE-PRESENCE GATE (2026-06-13) — the fix for the L3Event batch disaster.
# Five clips shipped with Speaker entirely OUT OF FRAME: the manifest had reframe.lock_x=true
# (pins the crop to one static X) on WIDE event footage where Speaker moves between whiteboards
# and the table. lock_x can't contain a moving subject, and when the tracker found no face it
# silently fell back to a static ROI-center crop — pointed at a whiteboard. NOTHING blocked it.
# qa_reframe_v2 already REPORTS the truth on stdout ("hits N (X%)" per segment, and an explicit
# "no face in ROI -> static ROI-center fallback" line). This gate reads that report and ABORTS
# the render when the crop isn't actually on a face — so a dead static crop can never ship again.
# Threshold: clip-wide weighted face-hit rate must be >= MIN_HIT_RATE, and no segment may be a
# pure no-face fallback. Verify-real-path + gate-don't-warn (memory 2026-06-11), applied to reframe.
MIN_HIT_RATE = 0.50  # standing tracked ~0.82, seated ~1.0; the dead-crop disasters were ~0.0


def _run_reframe_gated(cmd):
    """Run qa_reframe_v2, echo its report, and FAIL the render if the crop isn't on a face."""
    r = ff(cmd, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    print(out, end="" if out.endswith("\n") else "\n", flush=True)

    # Per-segment report lines: "...30.0fps 180f | hits 148 (82%) | x ... | y ... | center=box"
    seg_frames = [int(n) for n in re.findall(r"(\d+)f\s*\|\s*hits", out)]
    seg_hits = [int(h) for h in re.findall(r"hits\s+(\d+)\s*\(", out)]
    seg_fps = [float(f) for f in re.findall(r"([\d.]+)fps", out)]
    fallbacks = out.count("static ROI-center fallback")

    if seg_frames and seg_hits and len(seg_frames) == len(seg_hits):
        total_f = sum(seg_frames)
        total_hits = sum(seg_hits)
        rate = (total_hits / total_f) if total_f else 0.0
        worst = min((h / f) for h, f in zip(seg_hits, seg_frames) if f) if total_f else 0.0
        # A SUSTAINED out-of-frame stretch is a defect even if the clip-wide rate looks OK (one
        # good long segment can mask a fully-lost short one). Fail any segment >= 2s under 25% hits.
        # (A brief turn-to-the-whiteboard is tolerated; a multi-second dead crop is not.)
        fps = seg_fps[0] if seg_fps else 30.0
        bad_sustained = [
            (f, h) for f, h in zip(seg_frames, seg_hits)
            if f / fps >= 2.0 and (h / f) < 0.25
        ]
        if rate < MIN_HIT_RATE or fallbacks or bad_sustained:
            raise SystemExit(
                f"reframe FACE-PRESENCE GATE FAILED — the crop is NOT tracking a face "
                f"(clip-wide hit rate {rate*100:.0f}% < {MIN_HIT_RATE*100:.0f}%"
                f"{f', worst segment {worst*100:.0f}%' if seg_frames else ''}"
                f"{f', {len(bad_sustained)} sustained (>=2s) out-of-frame segment(s)' if bad_sustained else ''}"
                f"{f', {fallbacks} no-face fallback segment(s)' if fallbacks else ''}). "
                f"The subject is likely OUT OF FRAME. Causes: lock_x on moving footage, wrong "
                f"preset/ROI for this shot, or the subject is genuinely off this camera (use the "
                f"other angle or re-pick the cut). Fix the reframe config and re-render — "
                f"render ABORTED so a dead static crop can't ship (L3Event lesson, 2026-06-13)."
            )
    elif fallbacks:
        raise SystemExit(
            f"reframe FACE-PRESENCE GATE FAILED — {fallbacks} segment(s) fell back to a static "
            f"ROI-center crop (no face found). Subject is likely OUT OF FRAME. Render ABORTED."
        )
    else:
        # Couldn't parse a report — don't crash the render, but make the blind spot loud.
        print("  ⚠️ reframe face-presence gate: could not parse qa_reframe_v2 hit-rate report "
              "(format change?) — framing NOT verified this run.", flush=True)
    return r

# Same cache-correctness fix as captions.py (2026-06-12): fold the dependency scripts' content into
# VERSION so editing qa_reframe_v2.py / make_splitscreen.py auto-invalidates reframe caches
# instead of silently serving a stale crop.
def _dep_hash() -> str:
    h = hashlib.sha256()
    for p in (QA_REFRAME, MAKE_SPLIT):
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"missing:" + str(p).encode())
    return h.hexdigest()[:8]

VERSION = "2.7.0+" + _dep_hash()  # 2.7.0: per-segment chunks scene-split BY DEFAULT (cut→track→assemble at internal broadcast camera cuts, e.g. interviewer→guest-reaction inside one Q segment). 2.6.0: PER-SEGMENT mode. 2.5.0: FACE-PRESENCE GATE. 2.4.3: --global-y


def _count_frames(p) -> int:
    import subprocess
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                          "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True, check=True)
    return int(out.stdout.strip().splitlines()[0].strip().strip(","))  # ffprobe csv can trail a comma


def _assert_frames(p, expected: int, what: str):
    got = _count_frames(p)
    if got != expected:
        raise RuntimeError(f"reframe split: {what} has {got} frames, expected {expected} ({p}) — "
                           f"frame loss would silently desync the A/V splice; aborting stage")


def _tile_cmd(chunk, out, side_cfg, res, detw):
    """qa_reframe_v2 invocation for one split tile (ROI-restricted to one subject)."""
    cmd = [sys.executable, str(QA_REFRAME), str(chunk), str(out),
           "--preset", side_cfg.get("preset", "guest"), "--res", res,
           "--no-scene-split", "--lock-x", "--detw", str(detw)]
    if side_cfg.get("roi"):
        cmd += ["--roi"] + [str(v) for v in side_cfg["roi"]]
    if side_cfg.get("zoom") is not None:
        cmd += ["--zoom", str(side_cfg["zoom"])]
    if side_cfg.get("eye_y") is not None:
        cmd += ["--eye-y", str(side_cfg["eye_y"])]
    return cmd


def run(work_dir, config, inputs, inputs_meta, project, manifest, out_path):
    cut_out = inputs.get("cut") or (list(inputs.values())[-1] if inputs else None)
    if not cut_out:
        raise RuntimeError("reframe stage requires the cut stage output")

    preset = config.get("preset", "talking-head")
    zoom = config.get("zoom")
    res = str(config.get("res", "1080"))
    eye_y = config.get("eye_y")
    scene_split = config.get("scene_split")
    split = config.get("split") or {}
    split_idxs = sorted(set(split.get("segments") or []))

    # PER-CUT, SINGLE PASS: reset the face tracker at each content seam so every cut is framed
    # independently (correct framing for wherever the subject is in THAT cut) — but in one render
    # pass (no concat glitches, no smoothing bleeding across seams). The seams come from the cut
    # stage's segment metadata (out_frame of each segment = first frame of the next). This is the
    # horizontal-to-vertical skill's locked "segment-aware single-pass" architecture. (The seams are
    # same-angle, so visual scene auto-detect can't see them — we pass them explicitly.)
    cut_meta = inputs_meta.get("cut", {})
    segs = cut_meta.get("segments") or []
    cut_frames = [int(s["out_frame"]) for s in segs[:-1] if "out_frame" in s] if len(segs) > 1 else []

    if split_idxs and not all(0 <= i < len(segs) for i in split_idxs):
        raise RuntimeError(f"reframe.split.segments {split_idxs} out of range for {len(segs)} cut segments")

    # ── PER-SEGMENT MODE (2026-06-13) — reframe each shot in ISOLATION, then concat. ──
    # For MULTI-CAMERA clips (interviewer cam → guest cam → split), the single-pass tracker reset
    # lands ~1 frame late at a camera cut AND the per-segment X-smoothing ramps into the new subject
    # over the first 2-3 frames — so the crop visibly "shoots across" from the old subject's position
    # to the new one's at the cut (Operator 2026-06-13: "keyframes shoot across from where Jay's at and
    # where Creator is at"). Reframing each segment as its own clip eliminates ALL cross-segment state:
    # each shot is face-tracked from its own frame 0 (edge-padded smoothing centers the subject
    # immediately), and the joins are clean HARD CUTS. Split segments still get the tile treatment.
    # Audio is taken UNTOUCHED from the cut output (video-only concat, then mux) — zero audio seams.
    # Default OFF: single-camera listicle/talking-head keeps the single-pass (no same-angle wobble).
    # Turn on with reframe.per_segment=true for multi-camera clips.
    if config.get("per_segment"):
        fps = float(cut_meta.get("fps") or 29.97)
        width = 2160 if res in ("4k", "4K", "2160") else 1080
        detw = int(split.get("detw", 2560))
        crop_y = int(split.get("crop_y", 192 if width == 2160 else 96))
        shadow = float(split.get("shadow_strength", 1.0))
        roi = config.get("roi")
        total_f = int(segs[-1]["out_frame"])
        seg_clips = []
        for i, s in enumerate(segs):
            in_f, out_f = int(s["in_frame"]), int(s["out_frame"])
            nf = out_f - in_f
            # Frame-exact, video-only chunk (libx264 full-decode select — no -ss B-frame leak, no VT tail drop).
            chunk = work_dir / f"{out_path.stem}_seg{i}_chunk.mp4"
            ff(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(cut_out),
                "-vf", f"select=between(n\\,{in_f}\\,{out_f - 1}),setpts=PTS-STARTPTS", "-an",
                "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(chunk)])
            _assert_frames(chunk, nf, f"segment {i} chunk")
            if i in split_idxs:
                top = work_dir / f"{out_path.stem}_seg{i}_top.mp4"
                bot = work_dir / f"{out_path.stem}_seg{i}_bottom.mp4"
                ff(_tile_cmd(chunk, top, split.get("top") or {}, res, detw))
                ff(_tile_cmd(chunk, bot, split.get("bottom") or {}, res, detw))
                seg_clip = work_dir / f"{out_path.stem}_seg{i}_section.mp4"
                ff([sys.executable, str(MAKE_SPLIT), "--speaker", str(top), "--guest", str(bot),
                    "--out", str(seg_clip), "--audio", "none", "--width", str(width),
                    "--crop-y", str(crop_y), "--guest-crop-y", str(crop_y),
                    "--shadow-strength", str(shadow)])
            else:
                # PER-SEGMENT OVERRIDE: when different content segments are DIFFERENT camera angles
                # (e.g. a wide multi-person shot where the subject is small + off to one side, then a
                # clean close-up), a single global ROI/zoom can't frame the subject in both. seg_overrides
                # maps a segment index -> {roi, zoom, eye_y, preset} so each angle is framed correctly.
                # (Clip 2 Operator-shoutout, 2026-06-13: segA = wide 3-shot, Speaker tiny at x=0.17 → left ROI
                # + zoom; segB = Speaker close-up → default. One ROI couldn't do both.)
                ov = (config.get("seg_overrides") or {}).get(str(i)) or {}
                s_preset = ov.get("preset", preset)
                s_zoom = ov.get("zoom", zoom)
                s_eye = ov.get("eye_y", eye_y)
                s_roi = ov.get("roi", roi)
                seg_clip = work_dir / f"{out_path.stem}_seg{i}_reframed.mp4"
                # CUT → FACE-TRACK → ASSEMBLE, applied WITHIN each chunk (Operator 2026-06-13):
                # a content segment routinely spans BROADCAST camera cuts the cut-list can't see —
                # e.g. an interviewer asks, the feed cuts to the guest REACTING before they answer
                # (Creator on Jay Shetty: Jay asks → cut to Creator reacting, all under one "question"
                # segment). If the chunk isn't scene-split, the new angle's frames get the PREVIOUS
                # subject's crop (Creator framed with Jay's tracking). So scene-split is the DEFAULT for
                # every per-segment chunk — the tracker resets at each internal camera cut. Set the
                # override scene_split:false ONLY for a known single-angle chunk with a fixed ROI
                # (e.g. a wide multi-person shot cropped to one subject) where a continuous crop is wanted.
                scene = "--no-scene-split" if ov.get("scene_split") is False else "--scene-split"
                scmd = [sys.executable, str(QA_REFRAME), str(chunk), str(seg_clip),
                        "--preset", s_preset, "--res", res, scene]
                if s_zoom is not None: scmd += ["--zoom", str(s_zoom)]
                if s_eye is not None: scmd += ["--eye-y", str(s_eye)]
                if s_roi: scmd += ["--roi"] + [str(v) for v in s_roi]
                if ov.get("detw"): scmd += ["--detw", str(ov["detw"])]  # bump for tiny faces on wide shots
                if ov.get("lock_x", config.get("lock_x")): scmd += ["--lock-x"]
                _run_reframe_gated(scmd)  # face-presence gate per shot
            _assert_frames(seg_clip, nf, f"segment {i} reframed clip")
            seg_clips.append(seg_clip)
        # Concat the reframed segment videos (hard cuts), then mux the cut output's audio untouched.
        fc_inputs = []
        for sc in seg_clips:
            fc_inputs += ["-i", str(sc)]
        fc_inputs += ["-i", str(cut_out)]  # audio source = last input
        streams = "".join(f"[{j}:v:0]" for j in range(len(seg_clips)))
        fc = streams + f"concat=n={len(seg_clips)}:v=1:a=0[v]"
        aud_idx = len(seg_clips)
        ff(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *fc_inputs,
            "-filter_complex", fc, "-map", "[v]", "-map", f"{aud_idx}:a?",
            *enc_v("20M"),
            "-c:a", "copy", "-movflags", "+faststart", str(out_path)])
        _assert_frames(out_path, total_f, "per-segment assembly")
        return {"out": str(out_path), "meta": {
            "preset": preset, "zoom": zoom, "res": res, "eye_y": eye_y,
            "roi": config.get("roi"), "lock_x": bool(config.get("lock_x")),
            "per_segment": True, "split_segments": split_idxs or None,
            "fps": cut_meta.get("fps"), "total_duration_s": cut_meta.get("total_duration_s"),
        }}

    full_out = out_path.parent / (out_path.stem + "_fullpass.mp4") if split_idxs else out_path

    cmd = [sys.executable, str(QA_REFRAME), str(cut_out), str(full_out),
           "--preset", preset, "--res", res]
    if zoom is not None: cmd += ["--zoom", str(zoom)]
    if eye_y is not None: cmd += ["--eye-y", str(eye_y)]
    # ROI override — WHERE in the source to look for the face. The preset ROIs assume a fixed shot
    # (e.g. stage tops out at y=0.55, fine for a STANDING speaker but misses a SEATED face at
    # ~0.70). On wide event footage where the subject moves (stands at a board / sits at a table),
    # pass a generous ROI that covers every position, e.g. [0.0, 0.10, 1.0, 0.82]. (Added 2026-06-13
    # with the face-presence gate — the seated L3 clips got 0% hits under the stock stage ROI.)
    roi = config.get("roi")
    if roi:
        if len(roi) != 4:
            raise RuntimeError(f"reframe.roi must be [x0,y0,x1,y1], got {roi!r}")
        cmd += ["--roi"] + [str(v) for v in roi]
    # detw — face-DETECTION width. Default suits 4K close shots; a WIDE 1080p angle where the
    # subject is small/distant (e.g. the A-cam rescue) needs a high detw (e.g. 1920) or YuNet
    # misses the tiny face. (Added 2026-06-13 with the A-cam two-angle rescue.)
    if config.get("detw"):
        cmd += ["--detw", str(int(config["detw"]))]
    if config.get("detw"):
        cmd += ["--detw", str(config["detw"])]  # bump detection width for small faces on wide event shots
    if config.get("lock_x"):
        cmd += ["--lock-x"]  # seated talking-head/podcast: pin X to the per-segment median (house rule 2026-06-12)
    # Y scope: a single locked camera (single/listicle pipelines) keeps ONE clip-wide eyeline so
    # same-angle seams don't bump the subject vertically; true multicam (qa/podcast) keeps per-angle
    # Y. Override per clip with config {"y_scope": "clip"|"segment"}.
    pipeline = (manifest or {}).get("pipeline", "")
    y_scope = config.get("y_scope") or ("clip" if pipeline in ("single", "listicle") else "segment")
    # MULTICAM override: an EXPLICIT reframe.scene_split=true means the source is a multi-camera
    # broadcast (interview/event with angle cuts every few seconds). Auto-detect EVERY camera cut on
    # the concatenated clip and frame each angle independently — this is the podcast preset's intended
    # mode. It takes precedence over content cut_frames: the content-join seams are themselves visual
    # cuts the detector catches, and the broadcast's camera cuts (which cut_frames can't see) MUST get
    # a tracker reset or the crop "pans across" at every angle change (same bug class as the single-cam
    # seam, but firing every ~6s on multicam footage). Per-angle Y here (no --global-y).
    if scene_split is True:
        cmd += ["--scene-split"]
    elif cut_frames:
        cmd += ["--cut-frames", ",".join(str(f) for f in cut_frames)]
    elif scene_split is False:
        cmd += ["--no-scene-split"]
    # Scene-detection sensitivity — forwarded to qa_reframe_v2 (added 2026-06-17, Guest159). A lower
    # scene_threshold catches SUBTLE camera cuts the 0.085 default misses; when missed, two angles
    # collapse into one segment and the locked X jumps mid-"shot" → audit-visual's within-shot drift
    # gate (>8% dx) FAILS. min_seg debounces multi-frame dissolves. Optional + backward-compatible:
    # absent keys = qa_reframe_v2's own defaults (behaviour unchanged for every existing manifest).
    if config.get("scene_threshold") is not None:
        cmd += ["--scene-threshold", str(config["scene_threshold"])]
    if config.get("min_seg") is not None:
        cmd += ["--min-seg", str(int(config["min_seg"]))]
    # global-y: apply whenever y_scope=="clip" regardless of segment count.
    # Previously only applied when cut_frames was non-empty, so single-segment clips never got it —
    # that caused face-tracker Y-snaps at the moment the face was first detected (e.g. at t=1.1s
    # when the face enters the frame, the crop jumped from the default-center to the face position).
    # Now the global Y median is pre-computed and held from t=0 even on single-segment clips.
    if y_scope == "clip" and scene_split is not True:
        cmd += ["--global-y"]  # single-camera only; multicam scene-split wants per-ANGLE Y, not one clip-wide eyeline
    _run_reframe_gated(cmd)  # face-presence gate: aborts if the crop isn't on a face

    if split_idxs:
        fps = float(cut_meta.get("fps") or 29.97)
        width = 2160 if res in ("4k", "4K", "2160") else 1080
        detw = int(split.get("detw", 2560))
        crop_y = int(split.get("crop_y", 192 if width == 2160 else 96))
        shadow = float(split.get("shadow_strength", 1.0))
        sections = {}
        for i in split_idxs:
            in_f, out_f = int(segs[i]["in_frame"]), int(segs[i]["out_frame"])
            # Frame-exact chunk via select-by-frame-number (full decode; input -ss seeking drops
            # the first ~2 frames after the seek point on B-frame sources — measured 2026-06-12).
            # Video-only: tile audio is discarded by make_splitscreen --audio none anyway, and the
            # final assembly passes the full-pass audio through untouched.
            # libx264, NOT VideoToolbox: VT's async tail flush dropped the last 2 selected frames
            # nondeterministically (measured 2026-06-12) — frame-exactness beats encode speed on
            # this short intermediate. The _assert_frames gate below makes any drop a loud failure.
            chunk = work_dir / f"{out_path.stem}_split{i}_chunk.mp4"
            ff(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(cut_out),
                "-vf", f"select=between(n\\,{in_f}\\,{out_f - 1}),setpts=PTS-STARTPTS",
                "-an",
                "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(chunk)])
            _assert_frames(chunk, out_f - in_f, f"segment {i} chunk")
            # PER-SEGMENT tile config: different split segments can come from DIFFERENT source angles
            # with the two subjects in different screen positions (e.g. an opening two-shot = Logan/Mike,
            # vs a mid-answer 3-shot = Speaker-left/Mike-right). split.overrides[<idx>] supplies that
            # segment's own {top,bottom,detw} tiles; falls back to the shared split.top/bottom.
            # (Operator 2026-06-14: split Speaker+Mike on the Operator-shoutout 3-shot, distinct from the
            # Logan+Mike open/close splits.)
            ov = (split.get("overrides") or {}).get(str(i)) or {}
            top_cfg = ov.get("top") or split.get("top") or {}
            bot_cfg = ov.get("bottom") or split.get("bottom") or {}
            seg_detw = int(ov.get("detw", detw))
            top = work_dir / f"{out_path.stem}_split{i}_top.mp4"
            bot = work_dir / f"{out_path.stem}_split{i}_bottom.mp4"
            ff(_tile_cmd(chunk, top, top_cfg, res, seg_detw))
            ff(_tile_cmd(chunk, bot, bot_cfg, res, seg_detw))
            section = work_dir / f"{out_path.stem}_split{i}_section.mp4"
            # PER-TILE crop_y: the two subjects often sit at different heights in their reframed
            # verticals (one leans back/sits high, the other low), so ONE shared crop_y can't center
            # both — the slice that centers the top subject leaves the bottom subject low (or vice
            # versa). top/bottom.crop_y override per tile (fall back to crop_y).
            # (Operator 2026-06-13: Logan sat low w/ headroom while Mike sat high — needed independent
            # vertical slices to center each in its square.)
            top_cy = int(top_cfg.get("crop_y", crop_y))
            bot_cy = int(bot_cfg.get("crop_y", crop_y))
            ff([sys.executable, str(MAKE_SPLIT), "--speaker", str(top), "--guest", str(bot),
                "--out", str(section), "--audio", "none", "--width", str(width),
                "--crop-y", str(top_cy), "--guest-crop-y", str(bot_cy),
                "--shadow-strength", str(shadow)])
            _assert_frames(section, out_f - in_f, f"segment {i} split section")
            sections[i] = section

        # Assemble: full-pass video with each split segment's frame range replaced by its section.
        # AUDIO: mapped straight from the full pass (-c:a copy) — video-only splice, no audio seams.
        fc_inputs = ["-i", str(full_out)]
        for i in split_idxs:
            fc_inputs += ["-i", str(sections[i])]
        parts, labels, n_in = [], [], 1
        pos = 0
        for i in split_idxs:
            in_f, out_f = int(segs[i]["in_frame"]), int(segs[i]["out_frame"])
            if in_f > pos:
                parts.append(f"[0:v]trim=start_frame={pos}:end_frame={in_f},setpts=PTS-STARTPTS[p{len(labels)}]")
                labels.append(f"[p{len(labels)}]")
            parts.append(f"[{n_in}:v]setpts=PTS-STARTPTS[p{len(labels)}]")
            labels.append(f"[p{len(labels)}]")
            n_in += 1
            pos = out_f
        total_f = int(segs[-1]["out_frame"])
        if pos < total_f:
            parts.append(f"[0:v]trim=start_frame={pos}:end_frame={total_f},setpts=PTS-STARTPTS[p{len(labels)}]")
            labels.append(f"[p{len(labels)}]")
        fc = ";".join(parts) + f";{''.join(labels)}concat=n={len(labels)}:v=1:a=0[v]"
        ff(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            *fc_inputs, "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
            *enc_v("20M"),
            "-c:a", "copy", "-movflags", "+faststart", str(out_path)])
        _assert_frames(out_path, total_f, "split assembly")

    return {"out": str(out_path), "meta": {
        "preset": preset, "zoom": zoom, "res": res, "eye_y": eye_y,
        "roi": roi, "lock_x": bool(config.get("lock_x")),
        "scene_split": scene_split,
        "split_segments": split_idxs or None,
        "fps": cut_meta.get("fps"),
        "total_duration_s": cut_meta.get("total_duration_s"),
    }}
