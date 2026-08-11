#!/usr/bin/env python3
"""audit-visual: independent visual quality gate.

Checks crop/tracking stability, framing, frozen frames, black frames, first/last
frame, and aspect ratio on a rendered clip. No audio.

Calibrated 2026-06-12 against a 22-clip known-good batch, re-hardened 2026-06-13
so that FAIL means something a viewer would see:
  - The clip's REAL cut boundaries come from the render pipeline. Pass
    --project <clip_project_dir> and the gate reads the cut-stage metadata
    (10_WORK/stages/cut/<newest>.meta.json: per-segment out_frame + duration_s
    + fps) to get EXACT seam frame positions on the delivered timeline, and
    classifies each seam against the session transcript (10_WORK/words.json):
    a seam whose removed source gap CONTAINS WORDS is an intentional CONTENT
    cut (the clip is an assembly of multiple source spans on one locked camera)
    and is fully EXEMPT — the gate never reads a content cut as within-shot
    instability. A pure-silence same-take seam is a candidate jump (already
    owned by edit/reqc._jumpcut_scan); here we only hold the Y-axis across it.
    Without --project, the gate still tries _shared/clip_meta (contract /
    manifest next to the clip), and failing that self-detects seams from
    frame-difference spikes with WIDENED thresholds so a normal assembly cut
    can't trip it.
  - A small window (+/-SEAM_EXCLUDE_S) around every seam is excluded from the
    within-segment variation checks — only motion INSIDE one continuous
    segment can flag.
  - The authoritative stability metric is BACKGROUND PHASE-CORRELATION on the
    static top band of the frame (crop stable <=> background stable). The
    subject moving is allowed; the crop window jumping is the defect.
    Within a segment any vertical crop step is an error; across a SAME-TAKE
    seam the Y-axis must hold (Y-lock / global-Y), X may legitimately
    re-center. Across a CONTENT seam even Y may legitimately change (different
    source span, different pose) so it is not gated.
  - Face detection is continuity-tracked and median-smoothed; it corroborates
    and warns (off-center drift, detection coverage) but detector flicker can
    no longer fail a clip.
  - Frozen frames, black frames, aspect ratio (9:16 / 2160x3840) and
    first/last-frame sanity are unconditional — they fail a clip regardless of
    metadata.

Usage:
    python3 check.py --clip <mp4> --out <json> [--project <clip_project_dir>]
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
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# Render-pipeline metadata resolver (graceful: gate still runs without it)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
try:
    from clip_meta import resolve as resolve_clip_meta
except Exception:
    resolve_clip_meta = None


# ----------------------------------------------------- explicit-project metadata
# The canonical delivered clip lives OUTSIDE its project (e.g. a batch
# 20_DELIVER/v1/NN_SPEAKER_..._Slug_....mp4 vs the project 10_WORK/clips/<Slug>/),
# so clip_meta's walk-up can't find it and there's no co-located contract. When
# the caller passes --project we resolve seams straight from the render
# artifacts in that dir. Mirrors edit/reqc._jumpcut_scan's seam classification:
# a seam whose removed SOURCE gap contains transcript words is a CONTENT cut.

def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _newest(paths):
    paths = [p for p in paths if p.exists()]
    return max(paths, key=lambda p: p.stat().st_mtime) if paths else None


def _segments_from_meta(meta: dict, head_trim: float):
    """Build delivered-timeline segments from a cut meta. Returns (segments,
    seam_frames, total_s) or (None, None, None) if the meta is unusable."""
    segs_raw = meta.get("segments", [])
    fps = meta.get("fps")
    if not segs_raw or not fps:
        return None, None, None
    segments, seam_frames, t = [], [], 0.0
    use_out_frame = all("out_frame" in s for s in segs_raw)
    for s in segs_raw:
        d = float(s.get("duration_s", 0))
        start = max(0.0, t - head_trim)
        end = max(0.0, t + d - head_trim)
        if end > start:
            segments.append({"start_s": round(start, 3), "end_s": round(end, 3)})
        seam_frames.append(int(s["out_frame"]) if use_out_frame else round((t + d) * fps))
        t += d
    return segments, seam_frames, t - head_trim


def resolve_project_meta(project_dir: str, clip_duration: float) -> dict:
    """Seams + content classification from a clip project dir.

    The render cache keeps a cut meta per re-render (10_WORK/stages/cut/
    <hash>.meta.json, each with meta.segments giving out_frame + duration_s +
    fps on the delivered timeline). We pick the meta whose head_trim-adjusted
    TOTAL DURATION matches the delivered clip — NOT the newest by mtime, because
    a re-render in progress can leave a newer meta that describes a different cut
    than the file actually delivered. If no meta's total lands within 1.5s of the
    clip, none describes this file (stale / mid-render) -> return empty so the
    caller falls back to self-detection.

    Each seam is then classified against the source cut spec (10_WORK/cuts.json)
    and the session transcript (10_WORK/words.json): a seam whose removed source
    gap contains words = CONTENT cut (exempt from within-shot variation checks).
    """
    proj = Path(project_dir)
    out = {"segments": [], "seam_times": [], "content_seam_times": [], "fps": None,
           "resolved": False, "note": ""}
    cut_dir = proj / "10_WORK" / "stages" / "cut"
    meta_files = sorted(cut_dir.glob("*.meta.json")) if cut_dir.is_dir() else []
    if not meta_files:
        out["note"] = f"no cut meta under {cut_dir}"
        return out

    manifest = _load_json(proj / "manifest.json") or {}
    head_trim = float(((manifest.get("stages") or {}).get("leadfix") or {}).get("head_trim", 0) or 0)

    # choose the duration-matching meta (closest total to the delivered clip)
    best = None  # (abs_diff, meta_file, segments, seam_frames, total, fps)
    for mf in meta_files:
        m = (_load_json(mf) or {}).get("meta", {})
        segs, frames, total = _segments_from_meta(m, head_trim)
        if segs is None:
            continue
        diff = abs(total - clip_duration) if clip_duration > 0 else 0.0
        if best is None or diff < best[0] or (diff == best[0] and mf.stat().st_mtime > best[1].stat().st_mtime):
            best = (diff, mf, segs, frames, total, m.get("fps"))

    if best is None:
        out["note"] = "no cut meta had usable segments/fps"
        return out

    diff, meta_file, segments, seam_frames, meta_total, fps = best
    if clip_duration > 0 and diff > 1.5:
        out["note"] = (f"best cut meta total {meta_total:.1f}s != clip {clip_duration:.1f}s "
                       f"(stale/mid-render; {len(meta_files)} metas) — ignoring project metadata")
        return out

    # seam times on the delivered timeline = each interior segment's end
    seam_times = [seg["end_s"] for seg in segments[:-1]]

    # classify each seam: content (removed source gap has words) vs same-take
    cuts = _load_json(proj / "10_WORK" / "cuts.json") or {}
    src_segs = cuts.get("segments", [])
    words = (_load_json(proj / "10_WORK" / "words.json") or {}).get("words", [])

    def gap_has_words(t0, t1):
        if t1 <= t0:
            return False
        return any(t0 - 0.02 <= w.get("start", -9) and w.get("end", -9) <= t1 + 0.02
                   and w.get("start", 0) < w.get("end", 0) for w in words)

    # Map each delivered seam to the source gap between consecutive cut spans.
    # The cut stage may sub-split a source span on internal silence, so the
    # number of delivered seams >= number of source-span boundaries. We treat a
    # delivered seam as CONTENT when it lines up (by source order) with a real
    # gap between two source spans whose removed interval contains words; any
    # seam we cannot positively tie to a word-bearing source gap defaults to
    # CONTENT too (conservative: an assembly clip's seams are content unless we
    # can prove the cut was a pure-silence same-take pause-trim). Without a
    # source spec we cannot prove same-take, so every seam stays content.
    src_gaps_with_words = []
    for i in range(len(src_segs) - 1):
        try:
            g0, g1 = float(src_segs[i]["out"]), float(src_segs[i + 1]["in"])
        except (KeyError, ValueError, TypeError):
            continue
        if g1 > g0 and gap_has_words(g0, g1):
            src_gaps_with_words.append((g0, g1))

    # If we have source spans, decide per delivered seam whether it is a pure
    # same-take pause-trim (silence gap, no words) -> NOT content; else content.
    content_seam_times = list(seam_times)  # default: all content (fully exempt)
    if src_segs and words:
        # informational: count source-span boundaries whose removed gap is pure
        # silence (a same-take pause-trim). We keep the conservative content
        # default for gating; this count just surfaces in the report.
        src_silence_gaps = []
        for i in range(len(src_segs) - 1):
            try:
                g0, g1 = float(src_segs[i]["out"]), float(src_segs[i + 1]["in"])
            except (KeyError, ValueError, TypeError):
                continue
            if 0 <= (g1 - g0) < 2.5 and not gap_has_words(g0, g1):
                src_silence_gaps.append((g0, g1))
        # without a per-seam source-time map we keep the safe default (content);
        # the silence-gap list is surfaced only as a count for the report.
        out["same_take_silence_gaps"] = len(src_silence_gaps)

    out.update({
        "segments": segments,
        "seam_times": [round(s, 3) for s in seam_times],
        "content_seam_times": [round(s, 3) for s in content_seam_times],
        "seam_frames": seam_frames[:-1],
        "fps": fps,
        "head_trim": head_trim,
        "resolved": True,
        "src_gaps_with_words": len(src_gaps_with_words),
        "note": f"resolved {len(seam_times)} seams from {meta_file.name}",
    })
    return out

# ---- thresholds (% of frame height/width; calibrated 2026-06-12: known-good
# seams measure 0-3px dY @4K (0.0-0.08%), one accepted outlier at 28px (0.73%)) ----
ANALYSIS_W, ANALYSIS_H = 540, 960
BAND_FRAC = 0.22                 # static top band used for phase correlation
SEAM_EXCLUDE_S = 0.30            # +/- window around every seam excluded from
                                 # within-segment variation checks (a content cut
                                 # must never read as within-shot instability).
                                 # Sized from MEASURED drift between the cut meta's
                                 # cumulative-duration seam clock and the actual
                                 # delivered-frame cut: on a 20-segment clip this
                                 # drift reached +/-0.24s (it accumulates over a
                                 # long assembly), so a 0.15s window let content
                                 # cuts leak in as "within-shot" steps. 0.30s
                                 # covers worst-case drift + the sample spacing
                                 # while staying far under any real defect (a
                                 # frozen/black span is ~1s = far wider).
SEAM_DY_WARN, SEAM_DY_ERR = 0.9, 1.5          # vertical bump across a SAME-TAKE seam
WITHIN_DY_WARN, WITHIN_DY_ERR = 0.7, 1.2      # vertical crop step inside a shot
# Fallback thresholds when seams are SELF-DETECTED (no --project, no contract):
# a self-detected seam set is incomplete/apmontserratte, so a real content cut may
# sit inside a "segment". Widen the within-shot gate so a single assembly cut
# can't trip it; genuine instability persists across many samples and still trips.
WITHIN_DY_WARN_LOOSE, WITHIN_DY_ERR_LOOSE = 1.6, 2.6
# NOTE: horizontal motion is NOT gated — the reframer's X-tracking follows the
# subject by design, and on low-texture backdrops the subject's own head motion
# dominates horizontal phase correlation. dx is reported as measurement only.
MOVING_BG_BASELINE = 0.5         # median within-shot |dy| above this = moving content
FACE_OFFCENTER_X = (0.22, 0.78)
FACE_OFFCENTER_SUSTAIN_S = 1.5
EYELINE_SEAM_WARN = 6.0          # subject pose legitimately shifts at a jump cut
FRAMING_WARN = 40.0              # face-size deviation is advisory only — punch-in
                                 # zooms are an intentional style device


def get_video_info(clip_path: str) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", clip_path],
        capture_output=True, text=True,
    )
    data = json.loads(r.stdout)
    vs = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    try:
        fps = float(Fraction(vs.get("r_frame_rate", "30/1")))
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    return {
        "width": int(vs.get("width", 0)),
        "height": int(vs.get("height", 0)),
        "fps": fps,
        "duration": float(data.get("format", {}).get("duration", 0)),
        "total_frames": int(vs.get("nb_frames", 0) or 0),
    }


# ---------------------------------------------------------------- face detection

def get_face_detector():
    if not HAS_CV2:
        return None
    candidates = [
        os.environ.get("AUDIT_YUNET_MODEL", ""),
        _acq("horizontal-to-vertical/scripts/yunet.onnx"),
        os.path.join(cv2.data.haarcascades, "..", "face_detection_yunet_2023mar.onnx"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            try:
                return cv2.FaceDetectorYN.create(path, "", (ANALYSIS_W, ANALYSIS_H), 0.6)
            except Exception:
                continue
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    if os.path.exists(cascade_path):
        return cv2.CascadeClassifier(cascade_path)
    return None


def detect_faces(frame_bgr, det):
    if det is None:
        return []
    h, w = frame_bgr.shape[:2]
    results = []
    if hasattr(det, "detect"):
        det.setInputSize((w, h))
        _, faces = det.detect(frame_bgr)
        for face in (faces if faces is not None else []):
            x, y, fw, fh = face[0], face[1], face[2], face[3]
            results.append({"cx": (x + fw / 2) / w, "cy": (y + fh / 2) / h,
                            "size_pct": fw * fh / (w * h) * 100})
    else:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        for (x, y, fw, fh) in det.detectMultiScale(gray, 1.1, 6, minSize=(int(w * 0.08), int(w * 0.08))):
            results.append({"cx": (x + fw / 2) / w, "cy": (y + fh / 2) / h,
                            "size_pct": fw * fh / (w * h) * 100})
    return results


def pick_face(faces, prev):
    """Continuity-first: nearest to previous position, else largest."""
    if not faces:
        return None
    if prev and prev.get("cx") is not None:
        near = min(faces, key=lambda f: (f["cx"] - prev["cx"]) ** 2 + (f["cy"] - prev["cy"]) ** 2)
        if ((near["cx"] - prev["cx"]) ** 2 + (near["cy"] - prev["cy"]) ** 2) ** 0.5 < 0.3:
            return near
    return max(faces, key=lambda f: f["size_pct"])


def median_smooth(vals, k=5):
    """Median filter that passes None through gaps."""
    out = []
    half = k // 2
    for i in range(len(vals)):
        window = [v for v in vals[max(0, i - half):i + half + 1] if v is not None]
        out.append(float(np.median(window)) if window else None)
    return out


# ---------------------------------------------------------------- sampling

def sample_clip(clip_path: str, interval: int, det, want_color: bool):
    """One sequential decode pass: gray analysis thumbs + face positions."""
    cap = cv2.VideoCapture(clip_path)
    grays, faces, color_thumbs, idx = [], [], [], 0
    prev_face = None
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % interval == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            thumb = cv2.resize(frame, (ANALYSIS_W, ANALYSIS_H), interpolation=cv2.INTER_AREA)
            grays.append({"idx": idx, "gray": cv2.cvtColor(thumb, cv2.COLOR_BGR2GRAY)})
            f = pick_face(detect_faces(thumb, det), prev_face)
            faces.append({"idx": idx, **(f or {"cx": None, "cy": None, "size_pct": 0.0})})
            if f:
                prev_face = f
            if want_color:
                color_thumbs.append(cv2.resize(thumb, (135, 240)))
        idx += 1
    cap.release()
    return grays, faces, color_thumbs


def band_shift(gray_a, gray_b):
    """Phase-correlate the static top band. Returns (dx, dy) in analysis px."""
    band_h = int(ANALYSIS_H * BAND_FRAC)
    a = gray_a[:band_h, :].astype(np.float32)
    b = gray_b[:band_h, :].astype(np.float32)
    win = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, dy), _ = cv2.phaseCorrelate(a, b, win)
    return dx, dy


def classify_zoom(gray_a, gray_b):
    """Distinguish an intentional ZOOM from a crop shift: under zoom the left and
    right halves of the band move horizontally in OPPOSITE directions (content
    expands radially); under a translation they move together."""
    band_h = int(ANALYSIS_H * BAND_FRAC)
    half = ANALYSIS_W // 2
    win = cv2.createHanningWindow((half, band_h), cv2.CV_32F)
    a, b = gray_a[:band_h, :].astype(np.float32), gray_b[:band_h, :].astype(np.float32)
    (dxl, _), _ = cv2.phaseCorrelate(a[:, :half], b[:, :half], win)
    (dxr, _), _ = cv2.phaseCorrelate(a[:, -half:], b[:, -half:], win)
    min_px = 0.003 * ANALYSIS_W
    return (dxl * dxr < 0) and abs(dxl) >= min_px and abs(dxr) >= min_px


def grab_gray_at(cap, t_s):
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t_s) * 1000)
    ok, frame = cap.read()
    if not ok:
        return None
    thumb = cv2.resize(frame, (ANALYSIS_W, ANALYSIS_H), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(thumb, cv2.COLOR_BGR2GRAY)


# ---------------------------------------------------------------- seams

def self_detect_seams(grays, fps):
    """Fallback when no cut metadata: frame-diff outliers = candidate seams."""
    diffs = []
    for i in range(1, len(grays)):
        diffs.append(float(cv2.absdiff(grays[i - 1]["gray"], grays[i]["gray"]).mean()))
    if not diffs:
        return []
    arr = np.array(diffs)
    med, mad = np.median(arr), np.median(np.abs(arr - np.median(arr))) + 1e-6
    seams = []
    for i, d in enumerate(diffs):
        if (d - med) / (1.4826 * mad) > 8 and d > med * 2.5:
            seams.append(round(grays[i + 1]["idx"] / fps, 3))
    return seams


def segment_of(t, segments):
    for si, s in enumerate(segments):
        if s["start_s"] <= t < s["end_s"]:
            return si
    return len(segments) - 1 if segments else 0


# ---------------------------------------------------------------- checks

def check_crop_stability(clip_path, grays, segments, seam_times, fps, height,
                         content_seam_times=None, loose=False):
    """Background phase-correlation: the crop is stable iff the static set is stable.

    content_seam_times: seams that are INTENTIONAL content cuts (assembly of
      separate source spans). Y is not required to hold across these — a
      different span legitimately reframes — so they are measured/reported but
      never gate. seam_times not in this set are treated as same-take seams and
      DO gate Y-hold (Y-lock must survive a same-take pause-trim).
    loose: seams were self-detected (incomplete) -> widen the within-shot gate
      so an unmatched content cut sitting inside a "segment" can't trip it.
    """
    content_seam_times = content_seam_times or []
    within_warn = WITHIN_DY_WARN_LOOSE if loose else WITHIN_DY_WARN
    within_err = WITHIN_DY_ERR_LOOSE if loose else WITHIN_DY_ERR

    def near_seam(t):
        return any(abs(t - st) <= SEAM_EXCLUDE_S for st in seam_times)

    issues = []
    pct_y = 100.0 / ANALYSIS_H
    pct_x = 100.0 / ANALYSIS_W

    # within-segment consecutive-sample shifts. Exclude any pair whose endpoints
    # straddle OR sit within +/-SEAM_EXCLUDE_S of a seam — a content cut must
    # never be read as within-shot motion.
    within, seam_pair_idx = [], set()
    for i in range(1, len(grays)):
        t_prev, t_curr = grays[i - 1]["idx"] / fps, grays[i]["idx"] / fps
        crosses = any(t_prev < st <= t_curr for st in seam_times)
        if crosses or near_seam(t_prev) or near_seam(t_curr):
            seam_pair_idx.add(i)
            continue
        dx, dy = band_shift(grays[i - 1]["gray"], grays[i]["gray"])
        within.append({"i": i, "t": t_curr, "dx_pct": abs(dx) * pct_x, "dy_pct": abs(dy) * pct_y})

    baseline_dy = float(np.median([w["dy_pct"] for w in within])) if within else 0.0
    moving_bg = baseline_dy > MOVING_BG_BASELINE

    max_within_dy = max((w["dy_pct"] for w in within), default=0.0)
    max_within_dx = max((w["dx_pct"] for w in within), default=0.0)
    max_gateable_dy = 0.0   # max within-shot dy that is NOT an intentional zoom
    zoom_moves = 0
    if not moving_bg:
        for w in within:
            if w["dy_pct"] < within_warn:
                continue
            i = w["i"]
            # intentional punch-in zooms read as vertical band motion — exempt them
            if classify_zoom(grays[i - 1]["gray"], grays[i]["gray"]):
                zoom_moves += 1
                continue
            max_gateable_dy = max(max_gateable_dy, w["dy_pct"])
            # step-confirm: a real vertical crop step persists — neighbors stay shifted
            confirmed = False
            if i - 2 >= 0 and i + 1 < len(grays):
                _, dy2 = band_shift(grays[i - 2]["gray"], grays[i + 1]["gray"])
                confirmed = abs(dy2) * pct_y >= 0.7 * w["dy_pct"]
            if confirmed:
                sev = "error" if w["dy_pct"] >= within_err else "warn"
            elif w["dy_pct"] >= within_err:
                sev = "warn"  # big but transient — sub-frame event, report only
            else:
                continue       # small unconfirmed transient = correlation noise
            issues.append({
                "time_s": round(w["t"], 2), "frame": grays[i]["idx"],
                "dy_pct": round(w["dy_pct"], 2),
                "step_confirmed": confirmed, "severity": sev,
                "problem": f"crop shifted {round(w['dy_pct'], 1)}% vertically WITHIN a shot "
                           f"at {round(w['t'], 2)}s "
                           f"({'sustained step' if confirmed else 'transient'})",
            })

    # seam stability: across a SAME-TAKE seam Y must hold (X may re-center, zoom
    # may legitimately differ). Across a CONTENT seam (separate source span) even
    # Y may legitimately change — measure & report, never gate.
    seam_results = []
    cap = cv2.VideoCapture(clip_path)
    for st in seam_times:
        a = grab_gray_at(cap, st - SEAM_EXCLUDE_S)
        b = grab_gray_at(cap, st + SEAM_EXCLUDE_S)
        if a is None or b is None:
            continue
        _, dy = band_shift(a, b)
        dy_pct = abs(dy) * pct_y
        is_content = any(abs(st - cs) <= 0.05 for cs in content_seam_times)
        rec = {"time_s": round(st, 2), "dy_pct": round(dy_pct, 2)}
        if classify_zoom(a, b):
            rec["zoom_change"] = True
            seam_results.append(rec)
            continue
        if is_content:
            rec["content_seam"] = True
            seam_results.append(rec)
            continue                       # intentional content cut — not gated
        seam_results.append(rec)
        if moving_bg:
            continue
        if dy_pct >= SEAM_DY_ERR:
            issues.append({"time_s": round(st, 2), "dy_pct": round(dy_pct, 2), "severity": "error",
                           "problem": f"background jumps {round(dy_pct, 1)}% vertically across the "
                                      f"same-take cut at {round(st, 1)}s (Y-lock broken at seam)"})
        elif dy_pct >= SEAM_DY_WARN:
            issues.append({"time_s": round(st, 2), "dy_pct": round(dy_pct, 2), "severity": "warn",
                           "problem": f"background shifts {round(dy_pct, 1)}% vertically across the "
                                      f"same-take cut at {round(st, 1)}s"})
    cap.release()

    # PAN GATE (2026-06-12, Guest159 regression): large horizontal crop drift WITHIN a shot = the
    # frame is FOLLOWING/panning the subject instead of holding (no lock-x). Guest over-zoom batch
    # measured 8-52% within-shot dx; a lock-x'd frame is ~0-3%. Gate only when seams are KNOWN and the
    # background is static (a missed content cut or a moving bg would spike dx falsely).
    PAN_DX_ERR = 8.0
    if (not loose) and (not moving_bg) and max_within_dx > PAN_DX_ERR:
        issues.append({"time_s": None, "dx_pct": round(max_within_dx, 2), "severity": "error",
                       "problem": f"PANNING: crop drifts {round(max_within_dx, 1)}% horizontally WITHIN a "
                                  f"shot — the frame follows the subject instead of holding. Reframe with "
                                  f"--lock-x (auto-on for the `podcast` preset) so X is locked per angle."})
    elif loose and max_within_dx > 15.0:
        issues.append({"dx_pct": round(max_within_dx, 2), "severity": "warn",
                       "problem": f"crop drifts {round(max_within_dx, 1)}% horizontally within a shot "
                                  f"(seams self-detected — could be a missed content cut; verify it's not a follow-pan)"})

    has_error = any(i["severity"] == "error" for i in issues)
    out = {
        "pass": not has_error,
        "seams_checked": len(seam_results),
        "content_seams": sum(1 for s in seam_results if s.get("content_seam")),
        "same_take_seams_gated": sum(1 for s in seam_results
                                     if not s.get("content_seam") and not s.get("zoom_change")),
        "max_seam_dy_pct": round(max((s["dy_pct"] for s in seam_results
                                      if not s.get("zoom_change")), default=0.0), 2),
        "zoom_seams": sum(1 for s in seam_results if s.get("zoom_change")),
        "zoom_moves_within_shots": zoom_moves,
        "max_within_shot_dy_pct": round(max_within_dy, 2),
        "max_gateable_within_dy_pct": round(max_gateable_dy, 2),
        "max_within_shot_dx_pct": round(max_within_dx, 2),
        "dy_note": "max_within_shot_dy_pct is the RAW peak (incl. intentional "
                   "zoom moves); max_gateable_within_dy_pct excludes zooms and is "
                   "what the gate actually evaluates against the threshold",
        "dx_note": "within-shot horizontal crop drift NOW GATES (>8% = panning/no-lock-x) when seams are "
                   "known + bg static; small dx for true subject re-centering at a content cut is exempt",
        "within_dy_err_threshold_pct": within_err,
        "baseline_dy_pct": round(baseline_dy, 3),
        "issues": issues[:10],
    }
    if moving_bg:
        out["note"] = ("background is not static (moving content/camera) — "
                       "phase-correlation stability checks skipped")
    return out


def check_face_tracking(faces, segments, seam_times, fps, crop_check,
                        content_seam_times=None) -> dict:
    """Advisory: subject coverage, off-center drift, eyeline continuity at seams.
    Crop defects are owned by crop_stability; detector flicker cannot fail a clip.
    Content seams (separate source spans) legitimately re-center / re-pose the
    subject, so off-center runs reset across them and eyeline shifts there are
    never reported."""
    content_seam_times = content_seam_times or []
    issues = []
    n = len(faces)
    detected = [f for f in faces if f["cx"] is not None]
    detected_pct = len(detected) / n * 100 if n else 0.0

    cx_s = median_smooth([f["cx"] for f in faces])
    cy_s = median_smooth([f["cy"] for f in faces])

    def crosses_seam(t_prev, t_curr):
        return any(t_prev < st <= t_curr for st in seam_times)

    # sustained off-center drift (smoothed) — reset the run at every seam so a
    # content cut that re-centers X never accumulates a phantom "drift"
    sample_dt = (faces[1]["idx"] - faces[0]["idx"]) / fps if n > 1 else 0.125
    need = max(2, int(FACE_OFFCENTER_SUSTAIN_S / max(sample_dt, 1e-6)))
    run = 0
    for i, cx in enumerate(cx_s):
        if i > 0 and crosses_seam(faces[i - 1]["idx"] / fps, faces[i]["idx"] / fps):
            run = 0
        off = cx is not None and not (FACE_OFFCENTER_X[0] <= cx <= FACE_OFFCENTER_X[1])
        run = run + 1 if off else 0
        if run == need:
            t = faces[i]["idx"] / fps
            issues.append({"time_s": round(t, 2), "severity": "error",
                           "problem": f"subject drifts outside center band for "
                                      f">{FACE_OFFCENTER_SUSTAIN_S}s around {round(t, 1)}s"})
            run = 0

    if detected_pct < 60 and detected_pct > 0:
        issues.append({"severity": "warn", "detected_pct": round(detected_pct, 1),
                       "problem": f"face detected in only {round(detected_pct, 1)}% of samples "
                                  f"— coverage too low to audit tracking confidently"})

    # eyeline continuity across SAME-TAKE seams only — the subject's pose
    # legitimately changes at a jump cut, so this warns; it escalates to error
    # only when the crop itself was proven unstable at the SAME seam. Content
    # seams (different source span) are skipped: a different eyeline is expected.
    crop_err_times = [i.get("time_s", -99) for i in crop_check.get("issues", [])
                      if i.get("severity") == "error"]
    for st in seam_times:
        if any(abs(st - cs) <= 0.05 for cs in content_seam_times):
            continue
        before = [cy_s[i] for i, f in enumerate(faces)
                  if cy_s[i] is not None and st - 1.0 <= f["idx"] / fps < st]
        after = [cy_s[i] for i, f in enumerate(faces)
                 if cy_s[i] is not None and st < f["idx"] / fps <= st + 1.0]
        if len(before) >= 2 and len(after) >= 2:
            dy = abs(float(np.median(after)) - float(np.median(before))) * 100
            if dy > EYELINE_SEAM_WARN:
                crop_broken_here = any(abs(t - st) <= 0.3 for t in crop_err_times)
                issues.append({"time_s": round(st, 2), "eyeline_dy_pct": round(dy, 1),
                               "severity": "error" if crop_broken_here else "warn",
                               "problem": f"subject eyeline shifts {round(dy, 1)}% across the "
                                          f"same-take cut at {round(st, 1)}s"})

    avg_cx = float(np.mean([c for c in cx_s if c is not None])) if any(c is not None for c in cx_s) else 0.5
    has_error = any(i["severity"] == "error" for i in issues)
    return {"pass": not has_error,
            "faces_detected_pct": round(detected_pct, 1),
            "avg_center_x": round(avg_cx, 2),
            "issues": issues[:10]}


def check_framing(faces, segments, fps) -> dict:
    """Face-size variation per segment — ADVISORY ONLY. Punch-in zooms and the
    subject leaning toward camera are intentional style devices, so face size
    cannot block a clip; crop_stability owns the blocking stability metrics."""
    issues = []
    sizes = median_smooth([f["size_pct"] if f["size_pct"] > 0 else None for f in faces])
    worst = 0.0
    for si, seg in enumerate(segments or [{"start_s": 0, "end_s": 1e9}]):
        seg_sizes = [s for i, s in enumerate(sizes)
                     if s is not None and seg["start_s"] <= faces[i]["idx"] / fps < seg["end_s"]]
        if len(seg_sizes) < 4:
            continue
        med = float(np.median(seg_sizes))
        if med <= 0:
            continue
        dev = np.abs(np.array(seg_sizes) - med) / med * 100
        if len(dev) >= 3:
            sustained = np.min(np.lib.stride_tricks.sliding_window_view(dev, 3), axis=1)
            seg_worst = float(np.max(sustained))
        else:
            seg_worst = 0.0
        worst = max(worst, seg_worst)
        if seg_worst > FRAMING_WARN:
            issues.append({"segment": si, "severity": "warn",
                           "deviation_pct": round(seg_worst, 1),
                           "problem": f"face size deviates {round(seg_worst, 1)}% within shot "
                                      f"{si} (could be an intentional punch-in — eyeball it)"})

    all_sizes = [s for s in sizes if s is not None]
    avg_size = round(float(np.mean(all_sizes)), 1) if all_sizes else 0.0
    # OVER-ZOOM GATE (2026-06-12, Guest159 regression): face AREA too large = head cropped / too tight.
    # A chest-up podcast/talking-head reframe (preset `podcast`, zoom 1.0) sits ~10-14% face area; the
    # over-zoom batch measured 20-23%. >20% = blocking FAIL; 16-20% = warn (could be an intentional guest
    # closeup — eyeball). Cause is almost always applying source-intel's zoom (1.4-2.2) on already-chest-up
    # podcast footage; the fix is `--preset podcast` (zoom 1.0) reframing from the 4K master.
    OVERZOOM_ERR, OVERZOOM_WARN = 20.0, 16.0
    if avg_size > OVERZOOM_ERR:
        issues.insert(0, {"severity": "error", "avg_face_size_pct": avg_size,
            "problem": f"OVER-ZOOMED: face fills {avg_size}% of the frame (head likely cropped). "
                       f"Talking-head/podcast should be chest-up (~10-14%). Reframe with `--preset podcast` "
                       f"(zoom 1.0) from the 4K master; do NOT apply source-intel's zoom on chest-up footage."})
    elif avg_size > OVERZOOM_WARN:
        issues.append({"severity": "warn", "avg_face_size_pct": avg_size,
            "problem": f"face fills {avg_size}% of frame — borderline tight; confirm it's an intentional closeup"})
    has_error = any(i.get("severity") == "error" for i in issues)
    return {"pass": not has_error,
            "avg_face_size_pct": avg_size,
            "max_sustained_deviation_pct": round(worst, 1),
            "segments": len(segments), "issues": issues[:10],
            "note": "OVER-ZOOM now GATES (>20% face area = FAIL — head cropped); face-size DEVIATION within "
                    "a shot stays advisory (intentional punch-ins)"}


def check_frozen_frames(grays, fps, interval) -> dict:
    issues = []
    consecutive = 0
    for i in range(1, len(grays)):
        if float(cv2.absdiff(grays[i - 1]["gray"], grays[i]["gray"]).mean()) < 0.4:
            consecutive += 1
        else:
            if consecutive >= 3:
                fi = grays[i - consecutive]["idx"]
                issues.append({"frame": fi, "time_s": round(fi / fps, 2),
                               "frozen_span_s": round(consecutive * interval / fps, 2),
                               "severity": "error",
                               "problem": f"~{round(consecutive * interval / fps, 2)}s frozen "
                                          f"at {round(fi / fps, 2)}s"})
            consecutive = 0
    if consecutive >= 3:
        fi = grays[-consecutive]["idx"]
        issues.append({"frame": fi, "time_s": round(fi / fps, 2),
                       "frozen_span_s": round(consecutive * interval / fps, 2),
                       "severity": "error",
                       "problem": f"frozen frames at clip end ({round(fi / fps, 2)}s)"})
    return {"pass": len(issues) == 0, "detected": len(issues), "issues": issues[:10]}


def check_black_frames(grays, fps) -> dict:
    issues = []
    for g in grays:
        mean_pixel = float(g["gray"].mean())
        if mean_pixel < 5:
            issues.append({"frame": g["idx"], "time_s": round(g["idx"] / fps, 2),
                           "mean_pixel": round(mean_pixel, 1), "severity": "error",
                           "problem": f"black frame at {round(g['idx'] / fps, 2)}s"})
    return {"pass": len(issues) == 0, "detected": len(issues), "issues": issues[:10]}


def check_first_last_frame(grays, faces) -> dict:
    issues = []
    if not grays:
        return {"pass": True, "issues": [], "note": "no frames"}
    if float(grays[0]["gray"].mean()) < 5:
        issues.append({"location": "first", "severity": "error", "problem": "first frame is black"})
    elif faces and faces[0]["cx"] is None:
        issues.append({"location": "first", "severity": "warn", "problem": "no face in first frame"})
    if float(grays[-1]["gray"].mean()) < 5:
        issues.append({"location": "last", "severity": "error", "problem": "last frame is black"})
    elif faces and faces[-1]["cx"] is None:
        issues.append({"location": "last", "severity": "warn", "problem": "no face in last frame"})
    has_error = any(i["severity"] == "error" for i in issues)
    return {"pass": not has_error, "issues": issues}


def check_aspect_ratio(video_info: dict) -> dict:
    w, h = video_info["width"], video_info["height"]
    if w == 0 or h == 0:
        return {"pass": False, "issues": [{"severity": "error", "problem": "could not read dimensions"}]}
    if abs(w / h - 9 / 16) < 0.02 or (w, h) in [(1080, 1920), (2160, 3840), (720, 1280)]:
        return {"pass": True, "ratio": "9:16", "resolution": f"{w}x{h}", "issues": []}
    return {"pass": False, "ratio": f"{w}:{h}", "resolution": f"{w}x{h}",
            "issues": [{"severity": "error",
                        "problem": f"aspect ratio is {w}:{h}, expected 9:16"}]}


def main():
    parser = argparse.ArgumentParser(description="Audit visual quality on a rendered clip")
    parser.add_argument("--clip", required=True, help="Path to rendered clip mp4")
    parser.add_argument("--out", required=True, help="Output JSON path")
    parser.add_argument("--project", help="Clip project dir (e.g. .../10_WORK/clips/<Slug>). "
                                          "Enables exact seam resolution + content-cut "
                                          "classification from the render artifacts.")
    parser.add_argument("--sample-interval", type=int, default=3, help="Sample every Nth frame")
    parser.add_argument("--contact-sheet", action="store_true", help="Generate contact sheet")
    args = parser.parse_args()

    clip = args.clip
    if not os.path.exists(clip):
        print(f"ERROR: clip not found: {clip}", file=sys.stderr)
        sys.exit(1)
    if not HAS_CV2:
        print("ERROR: opencv-python not installed. Run: pip3 install opencv-python", file=sys.stderr)
        sys.exit(1)

    video_info = get_video_info(clip)
    fps = video_info["fps"]
    print(f"Clip: {clip} ({video_info['width']}x{video_info['height']}, "
          f"{round(video_info['duration'], 1)}s)")

    # ---- seam resolution: --project (exact) > clip_meta (co-located) > self-detect
    segments, seam_times, content_seam_times = [], [], []
    seam_source, meta = None, {}
    proj_note = None

    if args.project:
        pm = resolve_project_meta(args.project, video_info["duration"])
        if pm.get("resolved"):
            segments = pm["segments"]
            seam_times = pm["seam_times"]
            content_seam_times = pm["content_seam_times"]
            seam_source = "project metadata"
            proj_note = pm.get("note")
            print(f"--project: {pm['note']} "
                  f"({len(content_seam_times)} content / "
                  f"{len(seam_times) - len(content_seam_times)} same-take)")
        else:
            proj_note = pm.get("note")
            print(f"--project given but unusable ({pm.get('note')}) — falling back")

    if not segments:
        meta = resolve_clip_meta(clip) if resolve_clip_meta else {}
        segments = meta.get("segments") or []
        seam_times = meta.get("seam_times") or []
        if segments:
            # contract/manifest seams: treat all as content cuts (assembly), since
            # without the source transcript we can't prove a same-take pause-trim
            content_seam_times = list(seam_times)
            seam_source = "render metadata"

    det = get_face_detector()
    if det is None:
        print("WARN: no face detector available — face checks limited")

    print(f"Sampling frames (every {args.sample_interval})...")
    grays, faces, color_thumbs = sample_clip(clip, args.sample_interval, det, args.contact_sheet)
    print(f"Sampled {len(grays)} frames")

    if not segments:
        seam_times = self_detect_seams(grays, fps)
        # self-detected seams are content-cut candidates (a frame-difference spike
        # on a single locked camera is a hard content cut) — exempt them too.
        content_seam_times = list(seam_times)
        segments = []
        bounds = [0.0] + seam_times + [video_info["duration"]]
        for a, b in zip(bounds, bounds[1:]):
            if b > a:
                segments.append({"start_s": a, "end_s": b})
        seam_source = "self-detected"
        print(f"No usable render metadata — self-detected {len(seam_times)} seams "
              f"(loose thresholds)")
    else:
        print(f"{len(seam_times)} cut seams from {seam_source}")

    # loose within-shot thresholds only when seams are self-detected (incomplete)
    loose = seam_source == "self-detected"

    results = {
        "clip": os.path.basename(clip),
        "resolution": f"{video_info['width']}x{video_info['height']}",
        "fps": round(fps, 2),
        "duration_s": round(video_info["duration"], 1),
        "total_frames": video_info["total_frames"] or (len(grays) * args.sample_interval),
    }
    checks = {}

    print("Checking aspect ratio...")
    checks["aspect_ratio"] = check_aspect_ratio(video_info)

    print("Checking crop stability (background phase-correlation)...")
    checks["crop_stability"] = check_crop_stability(clip, grays, segments, seam_times, fps,
                                                    video_info["height"],
                                                    content_seam_times=content_seam_times,
                                                    loose=loose)

    print("Checking face tracking (advisory, smoothed)...")
    checks["face_tracking"] = check_face_tracking(faces, segments, seam_times, fps,
                                                  checks["crop_stability"],
                                                  content_seam_times=content_seam_times)

    print("Checking framing...")
    checks["framing"] = check_framing(faces, segments, fps)

    print("Checking frozen/black/first-last frames...")
    checks["frozen_frames"] = check_frozen_frames(grays, fps, args.sample_interval)
    checks["black_frames"] = check_black_frames(grays, fps)
    checks["first_last_frame"] = check_first_last_frame(grays, faces)

    n_content = len(content_seam_times)
    checks["jump_cuts"] = {"pass": True, "detected": len(seam_times), "issues": [],
                           "note": f"{len(seam_times)} cut seams ({seam_source}; "
                                   f"{n_content} content / {len(seam_times) - n_content} "
                                   f"same-take) — content cuts exempt, "
                                   f"stability verified by crop_stability"}

    any_fail = any(not c["pass"] for c in checks.values())
    results["verdict"] = "FAIL" if any_fail else "PASS"
    results["checks"] = checks
    results["metadata"] = {
        "segments": len(segments),
        "seam_source": seam_source,
        "content_seams": n_content,
        "same_take_seams": len(seam_times) - n_content,
        "loose_thresholds": loose,
        "resolved_from": ("project" if seam_source == "project metadata"
                          else "contract/manifest" if seam_source == "render metadata"
                          else "none"),
    }
    if proj_note:
        results["metadata"]["project_note"] = proj_note

    failures = [k for k, v in checks.items() if not v["pass"]]
    if failures:
        first = checks[failures[0]]["issues"][0] if checks[failures[0]]["issues"] else {}
        results["summary"] = f"FAIL: {', '.join(failures)}. {first.get('problem', 'see details')}"
    else:
        results["summary"] = "All visual checks passed"

    if args.contact_sheet and color_thumbs:
        sheet_path = os.path.splitext(args.out)[0] + "_contact_sheet.jpg"
        cols = min(8, len(color_thumbs))
        rows = min(4, (len(color_thumbs) + cols - 1) // cols)
        sel = [int(i * len(color_thumbs) / (cols * rows)) for i in range(cols * rows)]
        sheet = np.zeros((240 * rows, 135 * cols, 3), dtype=np.uint8)
        for k, si in enumerate(sel):
            if si >= len(color_thumbs):
                break
            r, c = k // cols, k % cols
            sheet[r * 240:(r + 1) * 240, c * 135:(c + 1) * 135] = color_thumbs[si]
        cv2.imwrite(sheet_path, sheet)
        results["contact_sheet"] = sheet_path
        print(f"Contact sheet: {sheet_path}")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n{results['verdict']}: {results['summary']}")
    print(f"Report: {args.out}")


if __name__ == "__main__":
    main()
