#!/usr/bin/env python3
"""multicut.py --config CFG <ep> <t0_wav> <t1_wav> <out.mp4> [mode]
Cut a 2-person podcast clip to vertical 9:16 with angle-switching to the active speaker.

  mode: switch (angle-switch, default) | host | guest | split

Times <t0_wav>/<t1_wav> are HOST_WAV time (the diarized clean.json time-base). Uses
sync_map.json: source_time = wide_time + Δ;  wide_time = t_wav - Δ_host_wav.

Audio = synced clean lavs (amix), loudnorm I=-16. Video = per-speaker cam, 9:16 crop, concat
at speaker turns. RAW CUT ONLY — captions + music + jumpcut belong to multifinish.py.

LOCKED GOTCHAS (each cost a round; do not regress):
  - NEVER `-copyts`. It nulled the audio (empty bin_data stream). Seek-before-`-i` is
    frame-accurate in modern ffmpeg.
  - CLOSE DIARIZATION GAPS. A pause between two turns must extend the previous cam to the
    next turn's start (segs[i][1] = segs[i+1][0]); otherwise the concat drops gap-time and
    the VIDEO stream ends short of the (continuous) audio → last seconds black/frozen.
  - MERGE <1.2 s flicker segs into a neighbour; collapse consecutive same-speaker segs (no
    cam-strobing).
  - STRIP camera timecode data track on the mux (-dn -map_metadata -1). A trailing bin_data
    stream causes players to freeze/black at the end.
  - GUEST LAV seek must use guest-wav's Δ, not host-wav's, when the two lavs come from
    separate recorders. Convert host_wav-time → wide-time → guest_wav-time.
  - ENCODE via fast_encode.encoder_args() (VideoToolbox HW). NEVER hand-write libx264.
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
import json, subprocess, sys, argparse
from pathlib import Path
sys.path.insert(0, VIBE_SHARED)
from fast_encode import encoder_args   # Brand fast-render standard (VideoToolbox HW, ~4× vs libx264)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("ep")
    ap.add_argument("t0", type=float)
    ap.add_argument("t1", type=float)
    ap.add_argument("out")
    ap.add_argument("mode", nargs="?", default="switch",
                    choices=["switch", "host", "guest", "split"])
    a = ap.parse_args()
    cfg = json.loads(a.config.read_text())
    root = Path(cfg["project_root"]).expanduser()
    work = root / "10_WORK"
    files = cfg["episodes"][a.ep]

    # absolute paths to each input
    C = {k: str(root / v) for k, v in files.items()}
    SYNC = json.loads((work / "sync_map.json").read_text())
    s = SYNC[a.ep]
    dwav = float(s["host_wav"])
    dcam = {"host": float(s["host_cam"]), "guest": float(s["guest_cam"])}
    d_host_wav = float(s["host_wav"])
    d_guest_wav = float(s.get("guest_wav", d_host_wav))

    # Per-speaker reframe: punch-in zoom + normalized crop CENTER (cx,cy) in the source frame.
    # Higher cx → subject sits more LEFT in the output frame; higher cy → subject sits HIGHER.
    REFRAME = {
        "host":  cfg["speakers"]["host"].get("reframe",  {"zoom": 1.15, "cx": 0.50, "cy": 0.55}),
        "guest": cfg["speakers"]["guest"].get("reframe", {"zoom": 1.15, "cx": 0.50, "cy": 0.55}),
    }

    w0 = a.t0 - dwav                                                    # wide-time at clip start
    dur = a.t1 - a.t0

    # Speaker turns within [t0,t1] (clip-time), from the diarized clean transcript
    clean = json.loads((work / f"transcripts/{a.ep}_clean.json").read_text())
    in_range = [u for u in clean if u["end"] > a.t0 and u["start"] < a.t1]
    segs = []
    for u in in_range:
        aa = max(u["start"], a.t0) - a.t0
        bb = min(u["end"], a.t1) - a.t0
        spk = u["speaker"]
        if segs and segs[-1][2] == spk and aa - segs[-1][1] < 0.5:
            segs[-1][1] = bb
        else:
            segs.append([aa, bb, spk])
    if not segs:
        segs = [[0.0, dur, "host"]]
    segs[0][0] = 0.0
    segs[-1][1] = dur
    if a.mode in ("host", "guest"):
        segs = [[0.0, dur, a.mode]]

    # Merge any <1.2 s segment into a neighbour, then collapse consecutive same-speaker
    i = 0
    while len(segs) > 1 and i < len(segs):
        aa, bb, spk = segs[i]
        if bb - aa < 1.2:
            if i > 0:
                segs[i - 1][1] = bb
                segs.pop(i)
            else:
                segs[1][0] = aa
                segs.pop(0)
        else:
            i += 1
    coll = [segs[0]]
    for aa, bb, spk in segs[1:]:
        if spk == coll[-1][2]:
            coll[-1][1] = bb
        else:
            coll.append([aa, bb, spk])
    segs = coll

    # CLOSE GAPS so the concat covers the full [0,dur] (else video ends short of audio →
    # last seconds black/frozen — the AnxietyNeverLeaves bug). jumpcut later trims any
    # silent hold from BOTH streams equally, so this stays tight.
    for i in range(len(segs) - 1):
        segs[i][1] = segs[i + 1][0]
    segs[0][0] = 0.0
    segs[-1][1] = dur

    # Build inputs: one cam input per used speaker (seek-before-`-i`, no -copyts)
    used = sorted({spk for _, _, spk in segs})
    inputs = []
    for k in used:                                          # seek each cam to clip-start in ITS OWN time
        inputs += ["-ss", f"{w0 + dcam[k]:.3f}", "-i", C[f"{k}_cam"]]
    # Host lav: seek to t0 in host_wav time
    gi = len(used); inputs += ["-ss", f"{a.t0:.3f}", "-i", C["host_wav"]]
    # Guest lav: convert host_wav-time → wide-time → guest_wav-time. Same Δ for both lavs
    # (same-recorder case) is a no-op; this fixes the separate-recorder case correctly.
    guest_wav_t = a.t0 - d_host_wav + d_guest_wav
    ti = len(used) + 1; inputs += ["-ss", f"{guest_wav_t:.3f}", "-i", C["guest_wav"]]

    p, cat = [], ""
    for i, (aa, bb, spk) in enumerate(segs):
        src = used.index(spk)                                # cam input index (seeked to clip-start)
        rf = REFRAME[spk]
        ch = round(1080 / float(rf["zoom"]))
        cw = round(ch * 9 / 16)
        x0 = max(0, min(1920 - cw, round(float(rf["cx"]) * 1920 - cw / 2)))
        y0 = max(0, min(1080 - ch, round(float(rf["cy"]) * 1080 - ch / 2)))
        p.append(f"[{src}:v]trim={aa:.3f}:{bb:.3f},setpts=PTS-STARTPTS,"
                 f"crop={cw}:{ch}:{x0}:{y0},scale=1080:1920:flags=lanczos,setsar=1,fps=30[v{i}]")
        cat += f"[v{i}]"
    p.append(f"{cat}concat=n={len(segs)}:v=1:a=0[vout]")
    p.append(f"[{gi}:a][{ti}:a]amix=inputs=2:duration=first:normalize=0,highpass=f=70,"
             f"loudnorm=I=-16:TP=-1.5:LRA=11[aout]")

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(p),
           "-map", "[vout]", "-map", "[aout]",
           "-dn", "-map_metadata", "-1",                    # strip the cams' auto timecode data track
           "-t", f"{dur:.3f}",
           *encoder_args(1080, 1920, "ffmpeg", tier="intermediate"),
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", a.out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    turns = " ".join(f"{spk}({aa:.0f}-{bb:.0f})" for aa, bb, spk in segs)
    print(f"{'OK' if r.returncode == 0 else 'FAIL'} {a.out}  {dur:.0f}s  turns: {turns}")
    if r.returncode:
        print(r.stderr[-1200:])


if __name__ == "__main__":
    main()
