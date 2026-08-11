#!/usr/bin/env python3
"""Vibe-classified music picker — standard procedure for adding music to clips.

Classifies each clip into ONE of 4 vibe labels via Groq llama-3.3 based on its
transcript, then picks a track from that vibe's dedicated folder. Guarantees
unique tracks across the batch where pool size allows.

Vibe → folder mapping (tuned for short-form talking-head content):
  hype_money  — money talk, success, aggressive energy, winning, risk/reward
                → (2) Hip Hop - Trap
  viral_fun   — upbeat casual advice, everyday habits, lifestyle takes
                → (1) Tik Tok/(2) Core
  reflective  — personal story, emotional, introspective, memory, humility
                → (1) Tik Tok/(1) Calm
  cinematic   — philosophical, serious, framework-heavy, intellectual weight
                → Trending Music (2024)

Usage — standalone:
  python3 vibe_music.py \
      --clips-dir /path/to/final \
      --captions-dir /path/to/captions \
      --verticals-dir /path/to/vertical \
      --transcripts-dir /path/to/transcripts \
      --assets-root "/path/to/editing assests/..." \
      --out-log music_log.json

Usage — import:
  from vibe_music import classify_and_assign, render_with_music
  assignments = classify_and_assign(clip_transcripts, pools)
"""
from __future__ import annotations

# ── vibe-editing portable path bootstrap (auto-inserted) ──
# ── engine bundled-keys autoload (config/keys.env) ──
import os as _ko, pathlib as _kp
def _acq_load_keys():
    d = _kp.Path(__file__).resolve()
    for p in (d, *d.parents):
        if (p / ".claude-plugin").is_dir():
            f = p / "config" / "keys.env"
            if f.is_file():
                for _ln in f.read_text().splitlines():
                    _ln = _ln.strip()
                    if _ln and not _ln.startswith("#") and "=" in _ln:
                        _k, _v = _ln.split("=", 1); _k, _v = _k.strip(), _v.strip()
                        if _k and "PASTE" not in _v and not _ko.environ.get(_k):
                            _ko.environ[_k] = _v
            return
_acq_load_keys()
# ── end keys ──
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
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


FFMPEG = sorted(glob.glob("/opt/homebrew/Cellar/ffmpeg-full/*/bin/ffmpeg"))[0] \
         if glob.glob("/opt/homebrew/Cellar/ffmpeg-full/*/bin/ffmpeg") \
         else shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or str(Path(FFMPEG).parent / "ffprobe")

# Brand FAST-RENDER STANDARD — VideoToolbox HW encode (~4x), resolution-aware single source of truth.
sys.path.insert(0, VIBE_SHARED)
try:
    from fast_encode import encoder_args_for
except Exception:
    encoder_args_for = None


# Default vibe → subfolder (relative to assets root)
DEFAULT_VIBE_FOLDERS = {
    "hype_money":  "(2) Hip Hop - Trap",
    "viral_fun":   "(1) Tik Tok/(2) Core",
    "reflective":  "(1) Tik Tok/(1) Calm",
    "cinematic":   "Trending Music (2024)",
    "default":     "(1) Tik Tok/(1) Calm",
}

CLASSIFY_SYSTEM = """You classify short-form podcast clips into ONE music vibe.
Output exactly ONE label, nothing else:

  hype_money   — money talk, success, aggressive energy, winning, risk/reward
  viral_fun    — upbeat casual advice, everyday habits, lifestyle takes
  reflective   — personal story, emotional, introspective, memory, humility
  cinematic    — philosophical, serious, framework-heavy, intellectual weight
"""


def load_zshrc_api_keys():
    """Lift API_KEY/TOKEN exports from ~/.zshrc into os.environ."""
    zshrc = Path.home() / ".zshrc"
    if not zshrc.exists():
        return
    for line in zshrc.read_text().splitlines():
        m = re.match(r'^\s*export\s+([A-Z_][A-Z0-9_]*)=(.*)$', line)
        if m and ("API_KEY" in m.group(1) or "TOKEN" in m.group(1)):
            val = m.group(2).strip().strip('"').strip("'")
            os.environ.setdefault(m.group(1), val)


def classify_vibe(transcript_text: str) -> str:
    """Groq llama-3.3 classifier. Falls back to 'default' on any error."""
    if not transcript_text.strip():
        return "default"
    try:
        from openai import OpenAI
    except ImportError:
        return "default"
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return "default"
    client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")
    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            temperature=0, max_tokens=20,
            messages=[
                {"role": "system", "content": CLASSIFY_SYSTEM},
                {"role": "user", "content": f"Clip text:\n{transcript_text[:2000]}"},
            ],
        )
        label = (resp.choices[0].message.content or "").strip().lower()
        for k in DEFAULT_VIBE_FOLDERS:
            if k == label:
                return k
        for k in DEFAULT_VIBE_FOLDERS:
            if k in label:
                return k
    except Exception as e:
        print(f"  [warn] classify error: {e}", file=sys.stderr)
    return "default"


def scan_music_library(assets_root: Path,
                       vibe_folders: dict[str, str] | None = None
                       ) -> dict[str, list[Path]]:
    """Scan each vibe folder, return {vibe: [mp3 Path, ...]} sorted."""
    mapping = vibe_folders or DEFAULT_VIBE_FOLDERS
    pools: dict[str, list[Path]] = {}
    for vibe, rel in mapping.items():
        folder = assets_root / rel
        if not folder.exists():
            print(f"  [warn] missing folder for '{vibe}': {folder}",
                  file=sys.stderr)
            pools[vibe] = []
            continue
        pools[vibe] = sorted(set(folder.rglob("*.mp3")),
                             key=lambda p: p.name.lower())
    return pools


def probe_duration(path: Path, cache: dict[Path, float] | None = None) -> float:
    if cache is not None and path in cache:
        return cache[path]
    try:
        out = subprocess.check_output([
            FFPROBE, "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(path)]).decode().strip()
        dur = float(out)
    except Exception:
        dur = 0.0
    if cache is not None:
        cache[path] = dur
    return dur


def detect_mono_source(video: Path) -> str:
    """Detect if a stereo video has voice on only one channel (common with
    separately-recorded mic WAVs that were wired to input 1 only).

    Returns:
      ""                         — both channels have audio, no remap needed
      "pan=stereo|c0=c0|c1=c0"   — left channel has voice, duplicate to both
      "pan=stereo|c0=c1|c1=c1"   — right channel has voice, duplicate to both
    """
    def chan_mean_db(ch: int) -> float:
        r = subprocess.run(
            [FFMPEG, "-hide_banner", "-i", str(video),
             "-af", f"pan=mono|c0=c{ch},volumedetect",
             "-vn", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        for line in r.stderr.split("\n"):
            if "mean_volume" in line:
                try:
                    return float(line.split(":")[-1].strip().split()[0])
                except Exception:
                    pass
        return -120.0

    left = chan_mean_db(0)
    right = chan_mean_db(1)
    # Threshold: if one channel is > 20 dB quieter than the other, treat as mono
    if abs(left - right) < 20:
        return ""
    if left > right:
        return "pan=stereo|c0=c0|c1=c0"   # left has voice
    return "pan=stereo|c0=c1|c1=c1"       # right has voice


def pick_track(clip_idx: int, clip_duration: float, vibe: str,
               pools: dict[str, list[Path]], used: set[Path],
               dur_cache: dict[Path, float]) -> Path | None:
    """Pick a track from the vibe pool. Prefer unused + long-enough tracks."""
    pool = pools.get(vibe) or pools.get("default") or []
    if not pool:
        for v in pools.values():
            pool.extend(v)
    if not pool:
        return None
    viable = [p for p in pool
              if probe_duration(p, dur_cache) >= clip_duration + 2.0]
    unused_viable = [p for p in viable if p not in used]
    if unused_viable:
        candidates = unused_viable
    elif viable:
        candidates = viable
    else:
        candidates = [p for p in pool if p not in used] or pool
    rng = random.Random(42 + clip_idx)
    return rng.choice(candidates)


def render_with_music(vertical: Path, ass: Path, music: Path, out: Path,
                      voice_gain: float = 1.4, music_gain: float = 0.12,
                      fonts_dir: Path | None = None,
                      loudnorm: bool = True):
    """Burn captions + mix music + loudnorm into a single ffmpeg call.

    Auto-detects mono-source voice (mic wired to one channel only) and
    duplicates the live channel to both output channels so voice plays
    on both speakers/earbuds.
    """
    dur = probe_duration(vertical)
    music_dur = probe_duration(music)
    fade_start = max(0.0, dur - 1.5)
    max_offset = max(0.0, music_dur - dur - 1.0)
    music_start = random.Random(vertical.stem + "vibe").uniform(
        0, min(max_offset, 30))

    fonts_arg = f":fontsdir='{fonts_dir}'" if fonts_dir else ""
    ass_esc = str(ass).replace(":", r"\:")
    vf = f"subtitles='{ass_esc}'{fonts_arg}"
    loud_suffix = ",loudnorm=I=-16:LRA=11:TP=-1.5" if loudnorm else ""

    # Detect mono-source voice (mic on one channel only) — prepend pan filter
    # and apply aggressive gain if detected (these mics typically record very low)
    mono_pan = detect_mono_source(vertical)
    if mono_pan:
        voice_chain = (
            f"[0:a]aresample=48000,{mono_pan},"
            f"highpass=f=80,volume=8.0,"
            f"loudnorm=I=-12:LRA=9:TP=-1,"
            f"dynaudnorm=g=31:r=0.9[voice]"
        )
    else:
        voice_chain = (
            f"[0:a]aresample=48000,volume={voice_gain},"
            f"highpass=f=80[voice]"
        )

    af = (
        f"{voice_chain};"
        f"[1:a]aresample=48000,atrim=start={music_start},asetpts=PTS-STARTPTS,"
        f"volume={music_gain},afade=t=in:st=0:d=0.8,"
        f"afade=t=out:st={fade_start}:d=1.5[mus];"
        f"[voice][mus]amix=inputs=2:duration=first:dropout_transition=0"
        f"{loud_suffix}[a]"
    )
    tmp = out.parent / f"_vibe_{out.name}"
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(vertical),
           "-stream_loop", "-1", "-i", str(music),
           "-filter_complex", f"[0:v]{vf}[v];{af}",
           "-map", "[v]", "-map", "[a]",
           # Brand fast-render standard (VideoToolbox HW, res-aware); libx264 fallback if _shared import fails.
           *(list(encoder_args_for(str(vertical), FFMPEG, tier="delivery")) if encoder_args_for
             else ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]),
           "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart",
           "-t", f"{dur:.3f}",
           str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"STDERR: {r.stderr[-1500:]}", file=sys.stderr)
        raise SystemExit(f"ffmpeg failed for {out.name}")
    tmp.replace(out)


def classify_and_assign(transcripts: dict[str, str],
                        pools: dict[str, list[Path]],
                        clip_durations: dict[str, float]
                        ) -> dict[str, dict]:
    """Main entry point: {name: text} + pools + durations → {name: assignment}."""
    used: set[Path] = set()
    dur_cache: dict[Path, float] = {}
    assignments = {}
    for i, name in enumerate(sorted(transcripts.keys())):
        text = transcripts[name]
        vibe = classify_vibe(text)
        clip_dur = clip_durations.get(name, 30.0)
        track = pick_track(i, clip_dur, vibe, pools, used, dur_cache)
        if track:
            used.add(track)
            assignments[name] = {
                "vibe": vibe,
                "track": track.name,
                "track_path": str(track),
            }
    return assignments


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcripts-dir", type=Path, required=True,
                    help="Directory with <name>.json transcripts")
    ap.add_argument("--verticals-dir", type=Path, required=True,
                    help="Directory with <name>.mp4 verticals (pre-music)")
    ap.add_argument("--captions-dir", type=Path, required=True,
                    help="Directory with <name>.ass caption files")
    ap.add_argument("--out-dir", type=Path, required=True,
                    help="Directory to write finals into")
    ap.add_argument("--assets-root", type=Path, required=True,
                    help="Music library root folder")
    ap.add_argument("--fonts-dir", type=Path, default=None)
    ap.add_argument("--log", type=Path, default=None,
                    help="Optional path to write the vibe log JSON")
    ap.add_argument("--dry-run", action="store_true",
                    help="Classify + assign only, don't render")
    args = ap.parse_args()

    load_zshrc_api_keys()
    pools = scan_music_library(args.assets_root)
    print("Music pool sizes:")
    for v, tracks in pools.items():
        print(f"  {v}: {len(tracks)}")

    # Collect transcripts + durations
    transcripts = {}
    clip_durations = {}
    for vf in sorted(args.verticals_dir.glob("*.mp4")):
        name = vf.stem
        tr_path = args.transcripts_dir / f"{name}.json"
        if tr_path.exists():
            tr = json.loads(tr_path.read_text())
            transcripts[name] = " ".join(w["word"] for w in tr.get("words", []))
        else:
            transcripts[name] = ""
        clip_durations[name] = probe_duration(vf)

    print(f"\nClassifying {len(transcripts)} clips...")
    assignments = classify_and_assign(transcripts, pools, clip_durations)

    print("\nAssignments:")
    for name, a in assignments.items():
        print(f"  {name[:42]:42}  vibe={a['vibe']:11}  "
              f"track={a['track'][:55]}")

    if args.log:
        args.log.write_text(json.dumps(assignments, indent=2))
        print(f"\nWrote {args.log}")

    if args.dry_run:
        return 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, a in assignments.items():
        vertical = args.verticals_dir / f"{name}.mp4"
        ass = args.captions_dir / f"{name}.ass"
        if not (vertical.exists() and ass.exists()):
            continue
        out = args.out_dir / f"{name}.mp4"
        t0 = time.time()
        render_with_music(vertical, ass, Path(a["track_path"]), out,
                          fonts_dir=args.fonts_dir)
        print(f"  ✓ {name[:42]:42}  {time.time()-t0:.1f}s")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
