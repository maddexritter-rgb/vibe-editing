#!/usr/bin/env python3
"""recut.py --config CFG --ep EpNN --title <Title> --music <file.mp3>
           --segments "<hook||payoff>" ["<hook||payoff>" ...]

Multi-segment HAND-CUT for the multicam-podcast-clipper. Each --segments arg is ONE keep-span,
resolved word-precise by its HOOK phrase (start) + PAYOFF phrase (end) against the episode's host
word transcript ({ep}_host.words.json). Append @SEC to a segment to bound the HOOK search near that
second (disambiguates a repeated hook). multicut each span (angle-switch) -> concat (VideoToolbox via
_shared/fast_encode.encoder_args) -> multifinish with a CONTINUOUS-time --speaker-map (guest=yellow /
host=white) so dual-color stays correct ACROSS the concat seam.

Use for CONTEXT-ADDS (seg1 = the submitted question/story, seg2 = the commentary) and INTERNAL cuts
(seg1 = before, seg2 = after a deleted middle). Auto-overwrites an existing rank-prefixed delivery in
place (no NN_ duplicates).

Brand-agnostic: project root, per-episode source/words paths, host/guest names, per-speaker reframe,
music dir and client_slug all come from --config (see config/example_config.json). NO brand names here.
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
import argparse, json, re, subprocess, sys
from pathlib import Path
sys.path.insert(0, VIBE_SHARED)
from fast_encode import encoder_args   # Brand fast-render standard (VideoToolbox HW, ~4x vs libx264)

SCRIPTS = Path(__file__).resolve().parent   # .../multicam-podcast-clipper/scripts (siblings live here)


def nz(w):
    return "".join(c for c in w.lower() if c.isalnum())


def find_start(W, phrase, lo, hi):
    t = [nz(x) for x in phrase.split() if nz(x)]
    for i, w in enumerate(W):
        if lo <= w["start"] <= hi and i + len(t) <= len(W) and all(nz(W[i + k]["word"]) == t[k] for k in range(len(t))):
            return W[i]["start"]
    return None


def find_end(W, phrase, lo, hi):
    t = [nz(x) for x in phrase.split() if nz(x)]
    for i in range(len(W) - len(t) + 1):
        j = i + len(t) - 1
        if lo <= W[j]["end"] <= hi and all(nz(W[i + k]["word"]) == t[k] for k in range(len(t))):
            return W[j]["end"] + 0.12   # +0.12s: speech onset labels ~0.1-0.25s early; keep the payoff tail
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--ep", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--music", required=True, help="filename in cfg.music_folder (or an absolute path)")
    ap.add_argument("--segments", required=True, nargs="+", metavar="hook||payoff",
                    help='one keep-span each: "<hook phrase>||<payoff phrase>" (append @SEC to bound the hook)')
    a = ap.parse_args()

    cfg = json.loads(a.config.read_text())
    root = Path(cfg["project_root"]).expanduser()
    work = root / "10_WORK"
    deliver = root / "20_DELIVER"
    renders = work / "renders"
    renders.mkdir(parents=True, exist_ok=True)
    slug = str(cfg.get("client_slug", "CLIENT")).upper()

    W = json.load(open(work / f"transcripts/{a.ep}_host.words.json"))["words"]
    clean = json.load(open(work / f"transcripts/{a.ep}_clean.json"))

    # 1. resolve each segment to a word-precise [start,end] keep-span (HAND-CUT hook->payoff)
    spans = []
    for seg in a.segments:
        hook, payoff = seg.split("||"); near = None
        if "@" in payoff:
            payoff, n = payoff.split("@"); near = float(n)
        s = find_start(W, hook.strip(), (near - 14) if near else 0, (near + 14) if near else 1e9)
        if s is None:
            sys.exit(f"X HOOK NOT FOUND: {hook}")
        e = find_end(W, payoff.strip(), s + 2, s + 220)
        if e is None:
            sys.exit(f"X PAYOFF NOT FOUND: {payoff}")
        spans.append((s, e))
    print("spans:", [(round(s, 1), round(e, 1), f"{e-s:.0f}s") for s, e in spans])

    # 2. multicut each span; build the guest(yellow) speaker-map in CONTINUOUS-clip time across the concat
    segclips, smap, off = [], [], 0.0
    for i, (s, e) in enumerate(spans):
        sc = renders / f"seg{i}_{a.title}.mp4"
        subprocess.run([sys.executable, str(SCRIPTS / "multicut.py"), "--config", str(a.config),
                        a.ep, f"{s}", f"{e}", str(sc), "switch"], capture_output=True, text=True)
        if not sc.exists():
            sys.exit(f"X multicut failed seg{i}")
        segclips.append(sc)
        for u in clean:
            if u["speaker"] == "guest" and u["end"] > s and u["start"] < e:
                smap.append([round(off + max(u["start"], s) - s, 2), round(off + min(u["end"], e) - s, 2)])
        off += (e - s)

    # 3. concat the segs into one continuous clip (VideoToolbox; captions+music belong to multifinish)
    combined = renders / f"cut_{a.title}.mp4"
    if len(segclips) == 1:
        subprocess.run(["cp", str(segclips[0]), str(combined)])
    else:
        inp = []; fc = ""
        for i, sc in enumerate(segclips):
            inp += ["-i", str(sc)]; fc += f"[{i}:v][{i}:a]"
        fc += f"concat=n={len(segclips)}:v=1:a=1[v][a]"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inp,
                        "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                        *encoder_args(1080, 1920, "ffmpeg", tier="intermediate"),
                        "-c:a", "aac", "-b:a", "192k", str(combined)], capture_output=True)

    capwork = work / "capwork"; capwork.mkdir(parents=True, exist_ok=True)
    smapf = capwork / f"{a.title}_smap.json"; smapf.write_text(json.dumps(smap))

    # 4. deliver -- auto-overwrite an existing rank-prefixed delivery IN PLACE (no NN_ duplicates)
    tt = a.title[0].upper() + a.title[1:]
    date = cfg.get("footage_date")
    if date:
        date = str(date).replace("-", "")
    else:
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", root.name)   # project folder is YYYY-MM-DD_<slug>
        date = "".join(m.groups()) if m else "00000000"
    deliver.mkdir(parents=True, exist_ok=True)
    base = f"{slug}_DTC_POD_{tt}_Operator_{date}_V1.mp4"
    existing = sorted(deliver.glob(f"*{slug}_DTC_POD_{tt}_Operator_*_V1.mp4"))
    out = existing[0] if existing else (deliver / base)

    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                         "-of", "csv=p=0", str(combined)]).strip())
    # multifinish with t0=0: the FINAL(tight)->clip mapping then matches the smap's continuous-clip time.
    r = subprocess.run([sys.executable, str(SCRIPTS / "multifinish.py"), "--config", str(a.config),
                        a.ep, "0", f"{dur}", str(combined), str(out),
                        "--music", a.music, "--speaker-map", str(smapf)], capture_output=True, text=True)
    print((r.stdout.strip().splitlines() or [""])[-1])
    normf = capwork / f"cut_{a.title}_norm.json"
    if normf.exists():
        w = json.load(open(normf))["words"]
        print(f"OPENS: {' '.join(x['word'] for x in w[:9])}")
        print(f"ENDS:  {' '.join(x['word'] for x in w[-6:])}")
    print(f"guest(yellow) ranges: {len(smap)}  ->  {out.name}")


if __name__ == "__main__":
    main()
