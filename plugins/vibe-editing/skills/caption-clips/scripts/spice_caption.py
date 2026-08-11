#!/usr/bin/env python3
"""spice_caption.py — the ONE "video in -> LOCKED spice-captioned video out" entry point.

THE SINGLE SOURCE OF TRUTH for burning the locked the reference editor/spice captions onto a clip:
  transcribe (Groq) -> spice_format (The reference editor's 9-rule text spec, timestamp-preserving) -> caption_director
  -> generate_spice --burn (two-layer gblur Premiere shadow, resolution-adaptive, per-word color) -> caption_qc.

It is the same proven chain the caption-app's caption_one.py runs; lifting it into the caption-clips
skill so the render skill (/edit step 8) and any other caller share ONE caption engine instead of
re-implementing a plain `subtitles=` burn (which shipped the wrong, shadow-less captions in /edit).

Usage:
  spice_caption.py <input.mp4> <output.mp4> [--context "..."] [--work DIR] [--preset PATH]

Resolution -> preset is auto-picked (spice.json >=2100px, else spice_1080.json) unless --preset given.
generate_spice is resolution-adaptive on top of that, so the shadow/animation land correctly at any res.
Requires GROQ_API_KEY (transcription). ANTHROPIC_API_KEY drives the director's color/emphasis; without
it the director falls back to deterministic styling (still spice, just no LLM-chosen guest color).

SPLIT MODES (2026-06-12) — the caption pipeline has two halves with different dependencies:
  • GENERATE (transcribe -> spice_format -> director) depends ONLY on the audio/words. It's the slow
    LLM part (~40-80s) and is IDENTICAL across any pixel-only change (reframe/zoom/grade/split).
  • BURN (layout on the video + generate_spice --burn) depends on the VIDEO PIXELS and must redo
    whenever the framing changes.
  --gen-only            run GENERATE on <input>'s audio, write transcript/spice_norm/director_stream
                        into --work, then STOP (no layout, no burn). <output> optional/ignored.
  --burn-from <gendir>  skip GENERATE; load spice_norm.json + director_stream.json from <gendir>;
                        run layout on <input> + generate_spice --burn -> <output>. No GROQ/LLM needed.
  (no flag)             FULL = gen then burn on the same input (the original, unchanged behavior).
The render `captions` stage uses these to cache GENERATE across pixel-only revisions (so a reframe
tweak reuses the styled caption file and skips the director re-roll — faster AND byte-consistent).
"""
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
import argparse, os, re, subprocess, sys, tempfile, time
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent          # caption-clips/
SC = SKILL / "scripts"
LAYOUT = SKILL.parent / "horizontal-to-vertical" / "scripts"  # layout analyzer lives with reframe assets
PRESETS = SKILL / "presets"


def load_key(name):
    v = os.environ.get(name)
    if v:
        return v
    zshrc = Path.home() / ".zshrc"
    if zshrc.exists():
        m = re.search(rf'(?m)^\s*export\s+{name}=["\']?([^"\'\n]+)', zshrc.read_text())
        if m:
            return m.group(1).strip()
    return None


def run(cmd, step, env, fatal=True):
    t = time.time()
    print(f"  → {step} …", flush=True)
    p = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if p.returncode != 0:
        print(f"  ✗ {step} FAILED (exit {p.returncode})", file=sys.stderr)
        print((p.stdout or "")[-1500:], file=sys.stderr)
        print((p.stderr or "")[-1500:], file=sys.stderr)
        if fatal:
            raise SystemExit(p.returncode)
    else:
        print(f"  ✓ {step}  ({time.time()-t:.1f}s)", flush=True)
    return p


def probe_height(path):
    h = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=height", "-of", "default=nokey=1:noprint_wrappers=1", str(path)],
                       capture_output=True, text=True).stdout.strip()
    return int(h) if h.isdigit() else 1920


def _probe_dur(path):
    d = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                       capture_output=True, text=True).stdout.strip()
    return float(d) if d else 0.0


def do_gen(inp, gendir, env, args):
    """GENERATE half — audio/words only, PIXEL-INDEPENDENT. Writes transcript.json, spice_norm.json,
    director_stream.json into gendir. This is the slow LLM part; cache it across pixel-only changes."""
    gendir.mkdir(parents=True, exist_ok=True)
    word = gendir / "transcript.json"
    spice = gendir / "spice_norm.json"
    stream = gendir / "director_stream.json"
    dur = _probe_dur(inp)

    if args.words and Path(args.words).exists():
        import shutil as _sh
        _sh.copyfile(args.words, word)
        print(f"spice_caption: pinned transcript {args.words} — transcription skipped", flush=True)
    else:
        run([sys.executable, str(SC / "transcribe_lv3.py"), str(inp), "--out", str(word),
             "--start", "0", "--end", f"{dur:.3f}"], "transcribe (Groq)", env)
    if args.corrections and args.corrections.exists():
        import json as _json, re as _re
        cmap = {k.lower(): v for k, v in _json.loads(args.corrections.read_text()).items()}
        wj = _json.loads(word.read_text())
        words = wj.get("words", [])
        def _bare(w): return _re.sub(r"^\W+|\W+$", "", w.get("word", ""))
        n_fixed = 0
        # PHRASE pass first: multi-token keys (e.g. "make common" -> "make.com", "and aiden" -> "and n8n")
        # merge the N matched tokens into ONE token spanning first.start..last.end. Whisper splits
        # spoken tech terms ("make.com", "n8n") into 2+ words, which token-level fixes can't rejoin.
        # Longest keys first so they win over shorter overlapping ones.
        for key in sorted((k for k in cmap if " " in k), key=lambda k: -len(k.split())):
            parts = key.split()
            i = 0
            while i <= len(words) - len(parts):
                if [_bare(words[i+j]).lower() for j in range(len(parts))] == parts:
                    merged = dict(words[i]); merged["word"] = cmap[key]
                    last = words[i+len(parts)-1]
                    if "end" in last: merged["end"] = last["end"]
                    words[i:i+len(parts)] = [merged]
                    n_fixed += 1
                else:
                    i += 1
        # TOKEN pass: single-token 1:1 fixes (e.g. "8n" -> "n8n")
        for w in words:
            bare = _bare(w)
            rep = cmap.get(bare.lower())
            if rep is not None:
                w["word"] = w["word"].replace(bare, rep)
                n_fixed += 1
        wj["words"] = words
        word.write_text(_json.dumps(wj))
        print(f"spice_caption: corrections applied ({n_fixed} word(s))", flush=True)
    # Align word.start to the TRUE acoustic onset (ffmpeg silencedetect) BEFORE formatting/chunking.
    # Fixes the premature-reveal bug (2026-06-14): Whisper/Groq place a post-pause word's start INSIDE
    # the preceding silence, so its caption flashes up before it's spoken. align_to_silence pushes any
    # word whose start falls in a detected silence to silence_end, so downstream timing/chunking sees
    # real onsets. fatal=False: if silencedetect finds nothing, words pass through unchanged.
    # SKIP when the transcript is PINNED (--words): a hand-supplied transcript carries authoritative
    # times already; align can mis-detect a quiet trailing word as silence and shove it ~1s late
    # (Studio ShovelVsBulldozer 2026-06-17 — pinned "done?"@21.8 got pushed to 22.7, delaying the cue
    # and overflowing the tail). Pinned times win; align only helps RAW ASR output.
    pinned = bool(args.words and Path(args.words).exists())
    if not pinned:
        run([sys.executable, str(SC / "align_to_silence.py"), "--in", str(word), "--out", str(word),
             "--audio", str(inp)], "align-to-silence (onset)", env, fatal=False)
    else:
        print("spice_caption: pinned transcript — skipping align-to-silence (pinned times authoritative)", flush=True)
    # ONE deterministic caption-text formatter (The reference editor's rules), timestamp-preserving.
    run([sys.executable, str(SC / "spice_format.py"), "--words", str(word), str(spice)],
        "spice-format (caption normalize)", env)
    ctx = args.context or (
        "the creator short-form clip. Read the TRANSCRIPT: IF a guest/caller/attendee asks a question "
        "or describes THEIR situation and Speaker answers, emit voice_spans over every contiguous GUEST line "
        "(rendered YELLOW); Speaker's lines stay WHITE. IF Speaker speaks alone, emit NO guest spans. If unsure, "
        "default to WHITE.")
    run([sys.executable, str(SC / "caption_director.py"), str(spice), "--out", str(stream), "--context", ctx],
        "director", env, fatal=False)
    return spice, stream


def do_burn(inp, out, gendir, work, env, args, preset):
    """BURN half — PIXEL-DEPENDENT. layout on the video + generate_spice --burn, using the spice_norm
    + director_stream produced by do_gen (from gendir). Re-runs whenever the framing changes."""
    spice = gendir / "spice_norm.json"
    stream = gendir / "director_stream.json"
    if not spice.exists():
        raise SystemExit(f"--burn-from: missing {spice} (run gen first)")
    work.mkdir(parents=True, exist_ok=True)
    ass = work / "subs.ass"
    # Caption Y source, in priority order:
    #   1. --layout-file PATH  : a pre-made per-segment layout (e.g. the render stage's auto per-section
    #                            layout: split panels on the seam, close-ups below the chin). Skip the analyzer.
    #   2. layout_analyze      : per-angle Y from the BURN input's pixels (default).
    #   3. --no-layout         : the preset's STATIC y_percent_from_top.
    layout = None
    if args.layout_file and Path(args.layout_file).exists():
        layout = Path(args.layout_file)
        print(f"spice_caption: using provided layout {layout.name} (per-section Y)", flush=True)
    elif not args.no_layout and (LAYOUT / "layout_analyze.py").exists():
        layout = work / "layout.json"
        run([sys.executable, str(LAYOUT / "layout_analyze.py"), str(inp), str(layout), "--sample-every", "1"],
            "layout analyze (per-angle Y)", env, fatal=False)
    if args.no_layout and not (args.layout_file and Path(args.layout_file).exists()):
        print("spice_caption: --no-layout -> using preset STATIC y_percent_from_top", flush=True)
    spice_cmd = [sys.executable, str(SC / "generate_spice.py"), str(spice), "--preset", str(preset),
                 "--out", str(ass), "--burn", str(inp), "--burn-out", str(out)]
    if stream.exists():
        spice_cmd.extend(["--style", str(stream)])
    if layout and Path(layout).exists():
        spice_cmd.extend(["--layout", str(layout)])
    run(spice_cmd, "render spice (generate_spice --burn, gblur shadow)", env)
    # GATE: prove this is the locked spice render (gblur sidecars + spice preset) — abort if not.
    sys.path.insert(0, str(SC))
    import caption_qc
    caption_qc.check_or_die(str(ass), str(preset), label="spice_caption qc")
    if not out.exists():
        raise SystemExit("generate_spice reported success but output missing")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output", nargs="?", default=None,
                    help="output mp4 (full/burn modes). Optional/ignored with --gen-only.")
    ap.add_argument("--gen-only", action="store_true", default=False,
                    help="run GENERATE (transcribe->format->director) into --work, then stop (no burn).")
    ap.add_argument("--burn-from", type=Path, default=None,
                    help="skip GENERATE; burn using spice_norm.json + director_stream.json from this dir.")
    ap.add_argument("--layout-file", type=Path, default=None,
                    help="pre-made per-segment layout.json (skip the analyzer); drives per-cue caption Y.")
    ap.add_argument("--context", default=None, help="director context (e.g. Q&A speaker-color hint)")
    ap.add_argument("--work", type=Path, default=None, help="work dir for intermediates (default tempdir)")
    ap.add_argument("--preset", type=Path, default=None, help="override preset (else auto by height)")
    ap.add_argument("--no-layout", action="store_true", default=False,
                    help="skip the per-angle layout analyzer; use the preset's STATIC y_percent_from_top. "
                         "Use for single-angle talking-head where a FIXED caption height is wanted (e.g. "
                         "Speaker desk clips pinned at the mic/tank-top line) instead of per-shot auto-height.")
    ap.add_argument("--words", type=Path, default=None,
                    help="pre-made word-level transcript JSON ({words:[{word,start,end}]}) for the "
                         "input clip — SKIPS the Groq transcription so the caption layer is "
                         "deterministic across re-renders (re-rolling ASR on crosstalk/laughter "
                         "hears different words each run; pin the good roll).")
    ap.add_argument("--corrections", type=Path, default=None,
                    help="JSON file of per-clip word-text fixes {\"heard\": \"burned\"} applied to the "
                         "transcription before formatting (case-insensitive match on the bare word; "
                         "punctuation preserved). For reviewer-flagged caption words the ASR mishears.")
    args = ap.parse_args()

    if args.gen_only and args.burn_from:
        raise SystemExit("--gen-only and --burn-from are mutually exclusive")

    inp = Path(args.input).resolve()
    if not inp.exists():
        raise SystemExit(f"input not found: {inp}")

    env = dict(os.environ)
    # GROQ is only needed when we actually transcribe (gen without pinned words). Burn-only never does.
    will_transcribe = (not args.burn_from) and not (args.words and Path(args.words).exists())
    groq = load_key("GROQ_API_KEY")
    if will_transcribe and not groq:
        raise SystemExit("missing GROQ_API_KEY (transcription) — not in env or ~/.zshrc")
    if groq:
        env["GROQ_API_KEY"] = groq
    ak = load_key("ANTHROPIC_API_KEY")
    if ak:
        env["ANTHROPIC_API_KEY"] = ak
    elif not args.burn_from:
        print("  ⚠ no ANTHROPIC_API_KEY — director uses deterministic styling (spice, no LLM color)", flush=True)

    work = args.work or Path(tempfile.mkdtemp(prefix="spicecap_"))
    work.mkdir(parents=True, exist_ok=True)

    # ONE preset for everything — generate_spice is resolution-adaptive, so spice.json (4K-calibrated)
    # scales itself to any frame (1080/4K/etc.). No spice_1080 variant: one style, no drift.
    preset = args.preset or (PRESETS / "spice.json")

    # ── GEN-ONLY: produce the styled caption file (audio/words only) and stop. ──
    if args.gen_only:
        print(f"spice_caption [gen-only]: {inp.name} -> {work}  (transcript/spice_norm/director_stream)", flush=True)
        do_gen(inp, work, env, args)
        print(f"✓ spice_caption gen done -> {work}", flush=True)
        return

    out = Path(args.output).resolve() if args.output else None
    if not out:
        raise SystemExit("output is required (full/burn modes)")
    out.parent.mkdir(parents=True, exist_ok=True)

    # ── BURN-FROM: reuse a cached gen dir; just layout + burn onto this (re-framed) video. ──
    if args.burn_from:
        gendir = Path(args.burn_from).resolve()
        print(f"spice_caption [burn-from {gendir.name}]: {inp.name} -> {out.name}  (preset {Path(preset).name})", flush=True)
        do_burn(inp, out, gendir, work, env, args, preset)
        print(f"✓ spice_caption burn done -> {out}  ({out.stat().st_size//1024} KB)", flush=True)
        return

    # ── FULL (default, unchanged behavior): gen then burn on the same input. ──
    print(f"spice_caption: {inp.name} -> {out.name}  (preset {Path(preset).name})", flush=True)
    do_gen(inp, work, env, args)
    do_burn(inp, out, work, work, env, args, preset)
    print(f"✓ spice_caption done -> {out}  ({out.stat().st_size//1024} KB)", flush=True)


if __name__ == "__main__":
    main()
