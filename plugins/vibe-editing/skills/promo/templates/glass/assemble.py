#!/usr/bin/env python3
"""ARCHIVE / ORIGIN-STORY FILM assembler (promo skill, MODE B).

Takes a long assembly cut whose chapters are separated by BLACK frames, plus a
set of Remotion era-cards + a growth-curve payoff (rendered from this glass
template), and stitches them into one branded film:

  card1 -> section1 -> card2 -> section2 -> ... -> card6 -> section6 -> payoff

Each section is colour-graded and gets a scene-matched music bed under the
dialogue; era cards carry a whoosh+impact SFX; the final section flashes to white
into the growth-curve payoff. The whole thing is loudness-normalised at the end.

HOW TO USE (per film):
  1. Copy this file into the film project's 10_WORK/ .
  2. Run `python3 ../<skill>/scripts/detect_sections.py <cut.mp4>` to get the
     content spans between the black separators; paste them into SECTIONS below.
  3. Sample a frame from each span and CONFIRM the chapter identity + YEAR with
     the user (dates are factual + audience-facing — never guess them).
  4. Render the era cards + GrowthCurve from the Remotion project (out/Card0N.mp4,
     out/GrowthCurve.mp4).
  5. Edit the CONFIG block below, then: `python3 assemble.py all`
     (or a single stage: s1..sN, cards, payoff, concat).

This is brand-agnostic: the cards/curve carry the brand (Remotion constants.ts);
this script only handles footage + music + SFX + the stitch.
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
import subprocess, os, sys
from pathlib import Path

# ═══════════════════════════ CONFIG — EDIT PER FILM ═══════════════════════════
P    = Path(os.environ.get("FILM_PROJECT", Path(__file__).resolve().parent.parent))  # project root (has 00_SOURCE/ 10_WORK/ 20_DELIVER/)
CUT  = P / "00_SOURCE/AssemblyCut.mp4"        # the long cut whose chapters are black-separated
H    = P / "10_WORK/<remotion-proj>"          # the Remotion project — out/ holds Card0N.mp4 + GrowthCurve.mp4
MUS  = _acqv("content-skill-system/(1) Tik Tok")   # music library root
PAYMUS = MUS / "(1) Calm/<emotional-build-track>.mp3"  # payoff bed (pick via pick_music; keep it in the approved lane, turned DOWN)
GRADE  = "eq=contrast=1.045:saturation=1.03,vignette=PI/4.3"   # subtle film-unify grade (no recolor)

# in, out (seconds in CUT), music file (None = NO music), music volume, flash-to-white tail (only the LAST section, into the payoff)
# Get in/out from detect_sections.py. Per-section music vol: ~0.17 default bed; 0.05 = "barely there"; 0.0/None = no music.
SECTIONS = [
    (1.30,  45.28,  MUS / "(1) Calm/<track-1>.mp3", 0.17, False),   # e.g. intro block (origin video -> snap -> co-founder)
    (170.85, 202.85, MUS / "(1) Calm/<track-2>.mp3", 0.17, False),  # e.g. the mission / why
    (245.21, 319.39, MUS / "(1) Calm/<track-3>.mp3", 0.17, False),  # e.g. milestone / book one
    (415.75, 431.99, None, 0.0, False),                             # e.g. a section the user wants with NO music
    (478.78, 528.14, MUS / "(1) Calm/<track-5>.mp3", 0.05, False),  # e.g. a section with a barely-there bed
    (579.79, 638.20, MUS / "(2) Core/<track-6>.mp3", 0.10, True),   # FINAL section -> turned down + flash into payoff
]
# ══════════════════════════════════════════════════════════════════════════════

SFX = H / "public/audio"
W   = P / "10_WORK/build"; W.mkdir(parents=True, exist_ok=True)
ENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2"]
NCARDS = len(SECTIONS)

def run(args):
    r = subprocess.run([str(a) for a in args], capture_output=True, text=True)
    if r.returncode != 0:
        print("FFMPEG ERR:", r.stderr[-1400:]); sys.exit(1)

only = sys.argv[1] if len(sys.argv) > 1 else "all"

# ---- SECTIONS: extract + grade + (optional) music bed under dialogue ----
# GOTCHA: -ss and -t must BOTH come BEFORE -i CUT (input trim). Placed after -i CUT they bind to the NEXT input (the music).
for i, (a, b, mf, vol, flash_out) in enumerate(SECTIONS, 1):
    if only not in ("all", f"s{i}"): continue
    dur = round(b - a, 3); fout = round(dur - 1.8, 2)
    vf = f"[0:v]fps=30,scale=1920:1080,setsar=1,{GRADE}"
    if flash_out:
        vf += f",fade=t=out:st={round(dur - 0.17, 3)}:d=0.17:color=white"   # snap to white into the payoff flash
    vf += "[v]"
    inp = ["-ss", a, "-t", dur, "-i", CUT]
    if mf is None:
        fc = f"{vf};[0:a]aresample=48000[a]"                                # dialogue only, no music
        label = "NO MUSIC"
    elif flash_out:
        wstart = int(round(dur - 1.0, 3) * 1000)                            # 1s whoosh ends right at the cut
        inp += ["-i", mf, "-i", SFX / "whoosh.mp3"]
        fc = (f"{vf};"
              f"[1:a]aloop=loop=-1:size=200000000,atrim=duration={dur},"
              f"afade=t=in:st=0:d=1.4,afade=t=out:st={fout}:d=1.8,volume={vol},aresample=48000[m];"
              f"[2:a]volume=0.6,adelay={wstart}|{wstart}[wh];"
              f"[0:a]aresample=48000[d];"
              f"[d][m][wh]amix=inputs=3:duration=first:normalize=0[a]")
        label = f"{mf.name} (vol {vol}) + flash-whoosh"
    else:
        inp += ["-i", mf]
        fc = (f"{vf};"
              f"[1:a]aloop=loop=-1:size=200000000,atrim=duration={dur},"
              f"afade=t=in:st=0:d=1.4,afade=t=out:st={fout}:d=1.8,volume={vol},aresample=48000[m];"
              f"[0:a]aresample=48000[d];"
              f"[d][m]amix=inputs=2:duration=first:normalize=0[a]")
        label = f"{mf.name} (vol {vol})"
    run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", *inp,
         "-filter_complex", fc, "-map", "[v]", "-map", "[a]", *ENC, W / f"sect{i}.mp4"])
    print(f"  ✓ sect{i} ({dur}s)  {label}")

# ---- ERA CARDS: add transition SFX (whoosh + soft impact) ----
# GOTCHA: NO -shortest. If the SFX is shorter than the card, -shortest truncates the card. Use apad + a hard -t = card length.
for i in range(1, NCARDS + 1):
    if only not in ("all", "cards"): continue
    fc = ("[0:v]fps=30,scale=1920:1080,setsar=1[v];"
          "[1:a]volume=0.5[w];[2:a]volume=0.42,adelay=110|110[im];"
          "[w][im]amix=inputs=2:duration=longest:normalize=0,apad[a]")
    run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", H / f"out/Card{i:02d}.mp4",
         "-i", SFX / "whoosh.mp3", "-i", SFX / "impact.mp3", "-filter_complex", fc,
         "-map", "[v]", "-map", "[a]", "-t", "2.6", *ENC, W / f"card{i}.mp4"])
    print(f"  ✓ card{i}")

# ---- PAYOFF: flash IN from white (transition from the last section), hold the curve's final frame, music bed + impact + braam ----
if only in ("all", "payoff"):
    fc = ("[0:v]fps=30,scale=1920:1080,setsar=1,fade=t=in:st=0:d=0.27:color=white,"
          "tpad=stop_mode=clone:stop_duration=4.7[v];"
          "[1:a]atrim=duration=11,volume=0.30,afade=t=in:st=0:d=1.5,afade=t=out:st=8.5:d=2.5[mu];"
          "[2:a]volume=0.42,adelay=4500|4500[br];"     # braam lands at the climax (tune adelay to your curve's stat reveal)
          "[3:a]volume=0.5[im];"                        # soft impact lands as the flash resolves into the graph
          "[mu][br][im]amix=inputs=3:duration=first:normalize=0[a]")
    run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", H / "out/GrowthCurve.mp4",
         "-i", PAYMUS, "-i", SFX / "braam.mp3", "-i", SFX / "impact.mp3", "-filter_complex", fc,
         "-map", "[v]", "-map", "[a]", "-shortest", *ENC, W / "payoff.mp4"])
    print("  ✓ payoff (flash-in + impact)")

# ---- CONCAT + loudnorm ---- ('concat' re-stitches existing pieces without rebuilding sections)
if only in ("all", "concat"):
    order = []
    for i in range(1, NCARDS + 1):
        order += [f"card{i}", f"sect{i}"]
    order += ["payoff"]
    lst = W / "concat.txt"
    lst.write_text("".join(f"file '{W}/{f}.mp4'\n" for f in order))
    run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", lst, "-c", "copy", W / "assembled_raw.mp4"])
    run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", W / "assembled_raw.mp4",
         "-af", "loudnorm=I=-15:TP=-1.5:LRA=11", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
         W / "film_assembled.mp4"])
    print("  ✓ ASSEMBLED -> 10_WORK/build/film_assembled.mp4  (QC, then copy to 20_DELIVER/)")
print("DONE")
