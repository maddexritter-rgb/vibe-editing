#!/usr/bin/env python3
"""
highlight_multicam.py — render a CAMERA-SWITCHED 16:9 Q&A mid from synced angles.

Inputs:
  --acam / --bcam   the host cam and the guest cam (16:9 source)
  --offset-b        BCAM_time = ACAM_time + offset_b  (from audio xcorr sync)
  --edl             JSON: {"blocks":[{"start","end","shot"}]} in ACAM time, CONTIGUOUS,
                    shot ∈ host | guest
  --out             finished switched mid (16:9, continuous ACAM audio @ -16 LUFS)

Shots:
  host   = the host cam, face-tracked to full 16:9
  guest  = the guest cam, face-tracked to full 16:9

Audio is ONE continuous track from ACAM (the cleanest board feed) so lip-sync holds while
the video switches. EDL blocks must be contiguous & cover the whole segment.
Renders each block, concats, muxes the continuous audio. Append the optional CTA after
(highlight_cta.py) or let highlight_cut.py handle it.
"""
# ── vibe-editing portable path bootstrap ──
import sys
import os as _os, sys as _sys
def _vibe_root():
    r = _os.environ.get("VIBE_PIPELINE_ROOT") or _os.environ.get("CLAUDE_PLUGIN_ROOT")
    if r and _os.path.isdir(_os.path.join(r, ".claude-plugin")):
        return r
    d = _os.path.dirname(_os.path.abspath(__file__))
    while d != _os.path.dirname(d):
        if _os.path.isdir(_os.path.join(d, ".claude-plugin")):
            return d
        d = _os.path.dirname(d)
    return _os.path.dirname(_os.path.abspath(__file__))
VIBE_ROOT = _vibe_root()
_sys.path.insert(0, _os.path.join(VIBE_ROOT, "lib", "_shared"))
REFRAME = _os.path.join(VIBE_ROOT, "skills", "highlight", "scripts", "highlight_reframe16.py")
# ── end bootstrap ──
import argparse, json, subprocess, tempfile, shutil
try:
    from fast_encode import encoder_args
except Exception:
    encoder_args = None


def venc(w, h):
    if encoder_args:
        try:
            return list(encoder_args(w, h, "ffmpeg", tier="intermediate"))
        except Exception:
            pass
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        _sys.exit(f"FAIL: {' '.join(str(c) for c in cmd[:8])} ...\n{r.stderr[-600:]}")


def shot_of(b):
    _sv = str(b.get("shot", b.get("speaker", "host"))).lower()  # tolerate shot/speaker keys + bcam/acam labels
    return "guest" if _sv in ("guest", "bcam", "b") else "host"


def measure_local_offset(ref_media, other_media, ref_time, coarse_off, sr=8000, win=30.0, search=5.0):
    """Re-measure a cam's sync offset LOCALLY at ref_time so OTHER_media (shown at ref_time+coarse_off)
    stays lip-synced despite camera clock DRIFT over a long recording. A single global offset measured
    at the recording start drifts ~0.1-0.4s by minute 50+ on un-genlocked cams; measuring per-segment
    kills that. Returns (offset, confidence); falls back to coarse_off on weak/failed correlation.
    Convention: other_cam_time = ref_cam_time + offset."""
    try:
        import warnings, numpy as np, librosa
        from scipy.signal import correlate, correlation_lags
        warnings.filterwarnings("ignore")  # quiet librosa/audioread mp4 fallback chatter
    except Exception:
        return coarse_off, 0.0
    try:
        o0 = max(0.0, ref_time + coarse_off); r0 = max(0.0, ref_time - search)
        other = librosa.load(other_media, sr=sr, mono=True, offset=o0, duration=win)[0]
        ref = librosa.load(ref_media, sr=sr, mono=True, offset=r0, duration=win + 2 * search)[0]
        if len(other) < sr or len(ref) <= len(other):
            return coarse_off, 0.0
        other = other / (np.max(np.abs(other)) + 1e-9); ref = ref / (np.max(np.abs(ref)) + 1e-9)
        corr = np.abs(correlate(ref, other, mode="valid"))
        lags = correlation_lags(len(ref), len(other), mode="valid")
        k = int(np.argmax(corr)); lag_s = lags[k] / sr
        med = float(np.sort(corr)[::-1][len(corr) // 2] + 1e-9)
        conf = float(corr[k] / med)
        true_off = o0 - (r0 + lag_s)   # = coarse_off + search - lag_s when unclamped
        return float(true_off), conf
    except Exception:
        return coarse_off, 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--acam", required=True)
    ap.add_argument("--bcam", required=True)
    ap.add_argument("--offset-b", type=float, required=True)
    ap.add_argument("--edl", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--W", type=int, default=1920)
    ap.add_argument("--H", type=int, default=1080)
    ap.add_argument("--fps", default="24")
    ap.add_argument("--host-roi", type=float, nargs=4, default=None, help="override host16 ROI [x0 y0 x1 y1] per-shoot (e.g. exclude a presentation slide)")
    ap.add_argument("--guest-roi", type=float, nargs=4, default=None, help="override guest16 ROI per-shoot")
    ap.add_argument("--guest-preset", default="guest16", help="reframe preset for guest blocks; use wide16 on rigs whose 'guest' cam is a wide audience/reaction cam, not a tight questioner cam")
    ap.add_argument("--host-offset", type=float, default=0.0, help="time offset for the HOST VIDEO cam when --acam is not the EDL/audio reference (host block seeks s + host_offset). Use when the tight-host cam differs from the board-audio cam.")
    ap.add_argument("--audio-cam", default=None, help="source cam for the continuous muxed audio (default: --acam). Set to the board-feed/reference cam when --acam is a different video angle.")
    ap.add_argument("--auto-sync", action=argparse.BooleanOptionalAction, default=True,
                    help="re-measure each non-reference cam's offset LOCALLY at the segment to defeat camera clock drift (DEFAULT ON). --no-auto-sync uses --offset-b/--host-offset verbatim.")
    a = ap.parse_args()

    data = json.load(open(a.edl)); blocks = data["blocks"] if isinstance(data, dict) else data
    W, H = a.W, a.H
    seg_start, seg_end = blocks[0]["start"], blocks[-1]["end"]; seg_dur = round(seg_end - seg_start, 3)
    VN = venc(W, H)
    tmp = tempfile.mkdtemp(prefix="hlmc_")
    parts = []
    try:
        # Two-cam FACE-TRACKED switch: each block = the speaker's cam, face-track-reframed to full
        # 16:9 (highlight_reframe16) so the moving subject stays framed.
        # DRIFT-PROOF SYNC: re-measure each non-reference cam's offset at THIS segment's time, not the
        # recording start. Single global offsets drift ~0.1-0.4s over a long un-genlocked multicam.
        audio_src = a.audio_cam or a.acam
        OB, OA = a.offset_b, a.host_offset
        if a.auto_sync:
            gb = next((b for b in blocks if shot_of(b) == "guest"), None)
            if gb is not None:
                off, conf = measure_local_offset(audio_src, a.bcam, float(gb["start"]), a.offset_b)
                if conf >= 4.0:
                    OB = off
                    print(f"[multicam] auto-sync GUEST @t={float(gb['start']):.0f}s: {a.offset_b:+.3f}s -> {off:+.3f}s (drift Δ{off-a.offset_b:+.3f}s, conf {conf:.1f})", flush=True)
                else:
                    print(f"[multicam] auto-sync GUEST: weak corr (conf {conf:.1f}) -> keep coarse {a.offset_b:+.3f}s", flush=True)
            if _os.path.realpath(a.acam) != _os.path.realpath(audio_src):  # host from a non-audio cam
                ab = next((b for b in blocks if shot_of(b) == "host"), None)
                if ab is not None:
                    off, conf = measure_local_offset(audio_src, a.acam, float(ab["start"]), a.host_offset)
                    if conf >= 4.0:
                        OA = off
                        print(f"[multicam] auto-sync HOST @t={float(ab['start']):.0f}s: {a.host_offset:+.3f}s -> {off:+.3f}s (drift Δ{off-a.host_offset:+.3f}s, conf {conf:.1f})", flush=True)
                    else:
                        print(f"[multicam] auto-sync HOST: weak corr (conf {conf:.1f}) -> keep coarse {a.host_offset:+.3f}s", flush=True)
        for i, b in enumerate(blocks):
            s, e = b["start"], b["end"]; dur = round(e - s, 3)
            shot = shot_of(b)
            out = f"{tmp}/b{i:03d}.mp4"; parts.append(out); clip = f"{tmp}/c{i:03d}.mp4"
            if shot == "guest":
                cam, ss, preset = a.bcam, s + OB, a.guest_preset
            else:  # "host" (any non-guest)
                cam, ss, preset = a.acam, s + OA, "host16"
            run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ss:.3f}", "-t", f"{dur:.3f}", "-i", cam,
                 "-an", "-c:v", "libx264", "-crf", "20", "-preset", "ultrafast", clip])
            _cmd = [sys.executable, REFRAME, clip, out, "--preset", preset, "--res", "1080"]
            _roi = a.guest_roi if shot == "guest" else a.host_roi
            if _roi:
                _cmd += ["--roi", *[f"{v}" for v in _roi]]
            run(_cmd)
            _os.remove(clip)
        # concat (re-encode for clean joins) then mux the continuous ACAM audio
        cl = f"{tmp}/concat.txt"; open(cl, "w").write("".join(f"file '{p}'\n" for p in parts))
        switched = f"{tmp}/switched.mp4"
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", cl,
             "-r", a.fps, *VN, switched])
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", switched,
             "-ss", f"{seg_start:.3f}", "-t", f"{seg_dur:.3f}", "-i", (a.audio_cam or a.acam),
             "-map", "0:v:0", "-map", "1:a:0", "-af", "loudnorm=I=-16:LRA=11:TP=-1.5",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", a.out])
        # SYNC VERIFY (advisory): re-derive guest lip-sync from the FINISHED mid vs the guest cam — should be ~0.
        try:
            gbk = max((b for b in blocks if shot_of(b) == "guest"),
                      key=lambda b: b["end"] - b["start"], default=None)
            if gbk is not None:
                exp = OB + seg_start
                got, c = measure_local_offset(a.out, a.bcam, float(gbk["start"]) - seg_start, exp)
                resid = got - exp
                ok = c < 4.0 or abs(resid) <= 0.08
                print(f"[multicam] sync-verify guest: residual {resid:+.3f}s (conf {c:.1f}) {'OK' if ok else '⚠️ OFF >80ms — check this mid'}", flush=True)
        except Exception:
            pass
        print(f"[multicam] ✅ {a.out}  ({len(blocks)} blocks, {seg_dur:.1f}s)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
