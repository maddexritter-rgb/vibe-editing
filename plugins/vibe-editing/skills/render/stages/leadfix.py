"""leadfix — trim a small amount off the head of the clip to remove the leading frame stall.
company.com SOP: ~3-5 frame lead, so trim ~0.063s (≈2 frames at 30fps) by default.

Config:
    { "head_trim": 0.063605 }   # seconds to trim from head (also re-encode at delivery bitrate)
    { "head_pad":  0.05 }       # OR: ADD this many sec of silent lead (held first frame + silent
                                #     audio) when the cut opens RIGHT on the first word (0 lead).
                                #     trim removes lead the source HAS; pad synthesizes it when there
                                #     is none. The SOP wants ~1.5-frame lead so the platform swipe
                                #     doesn't pop the first phoneme (sf #16). ~0.05s ≈ clip-1's
                                #     natural lead — passes sf #16 AND reqc's <=50ms head gate.
                                #     Mutually exclusive with head_trim (pad wins if both >0).
"""
from __future__ import annotations

from _util import enc_v, run as ff

VERSION = "1.1.0"  # 1.1.0: head_pad — synthesize a small silent lead when the cut has none (sf #16)


def run(work_dir, config, inputs, inputs_meta, project, manifest, out_path):
    prior = inputs[list(inputs.keys())[-1]]
    trim = float(config.get("head_trim", 0.063605))
    pad = float(config.get("head_pad", 0.0))

    if pad > 0:
        # Synthesize a short silent lead: hold the first frame for `pad` sec (tpad clone) and delay
        # the audio by the same so the first phoneme isn't on frame 0 (prevents the swipe-pop).
        ms = int(round(pad * 1000))
        fc = (f"[0:v]tpad=start_duration={pad:.3f}:start_mode=clone[v];"
              f"[0:a]adelay={ms}|{ms}[a]")
        ff(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", prior,
            "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
            *enc_v("12M", tier="delivery"),
            "-c:a", "aac", "-b:a", "192k", "-map_metadata", "-1", "-movflags", "+faststart", str(out_path)])
    elif trim <= 0:
        # No trim — just re-encode at delivery bitrate
        ff(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", prior,
            *enc_v("12M", tier="delivery"),
            "-c:a", "aac", "-b:a", "192k", "-map_metadata", "-1", "-movflags", "+faststart", str(out_path)])
    else:
        fc = (f"[0:v]trim=start={trim},setpts=PTS-STARTPTS[v];"
              f"[0:a]atrim=start={trim},asetpts=PTS-STARTPTS[a]")
        ff(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", prior,
            "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
            *enc_v("12M", tier="delivery"),
            "-c:a", "aac", "-b:a", "192k", "-map_metadata", "-1", "-movflags", "+faststart", str(out_path)])

    return {"out": str(out_path), "meta": {"head_trim": trim}}
