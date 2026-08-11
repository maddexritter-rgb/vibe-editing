#!/usr/bin/env python3
"""qa_build — deterministic, EDL-driven renderer for Speaker Q&A / hotline / DTC short-form.

The render used to live in docs + throwaway /tmp scripts and regressed every session.
This is the ONE tool. Give it an EDL; it produces a finished 1080x1920 clip that matches
the the reference editor house style (centered Speaker, 48% captions, yellow-italic guest / white Speaker).

EDL (json): {"segments":[ {"cam":"speaker|guest|wide", "mic_start":S, "mic_end":E,
                           "speaker":"speaker|guest"}, ... ]}
  cam     = which angle is ON SCREEN (speaker C2092 tracked / guest C2161 static / wide C2118 static)
  speaker = who is TALKING (drives caption color) — a guest-reaction cutaway is cam=guest,speaker=speaker
  mic_*   = source time on the mic/transcript timeline (cam_time = mic - offset, from qa_sync.json)

PIPELINE: per-segment fast-seek cut + reframe -> concat video -> 4-mic audio spliced to kept
runs (amix+dynaudnorm, 2-pass loudnorm -14) -> mux -> transcribe final -> SPICE captions
(generate_spice + spice_caption.json, color/italic per EDL speaker) -> HW H.264.

Usage:
  qa_build.py --edl EDL.json --out OUT.mp4 [--format qa|hotline|dtc]
              [--sync SYNC.json] [--preset PRESET.json] [--no-captions] [--keep-temp]
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
import os, sys, json, glob, shutil, subprocess, argparse, time

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # .../edit
CAPS  = _acq("caption-clips")
SHARED= VIBE_SHARED
sys.path.insert(0, SHARED)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # sibling helpers import under importlib too
from fast_encode import encoder_args
from speaker_diarize import resolve_speakers   # per-word Speaker/guest (mic energy + turn-taking)

REFRAME   = _acq("horizontal-to-vertical/scripts/qa_reframe_v2.py")
GEN_SPICE = f"{CAPS}/scripts/generate_spice.py"
NORMALIZE = f"{CAPS}/scripts/normalize_simple.py"
SPICE_NORM= f"{CAPS}/scripts/spice_normalize.py"
TRANSCRIBE= f"{CAPS}/scripts/transcribe_lv3.py"
FONTSDIR  = f"{CAPS}/fonts/free_font"

OW, OH, FPS = 1080, 1920, "30000/1001"
FADE = 0.030
# Per-cam reframe (locked, matches the the reference editor house style + horizontal-to-vertical SOP)
# Crops as iw/ih fractions of the source (res-independent -> works on 720p proxies AND 4K finals)
GUEST_CROP = "crop=iw*0.1758:ih*0.5556:iw*0.3034:ih*0.2606,scale=1080:1920,setsar=1"   # Stephanie centered
WIDE_CROP  = "crop=iw*0.3164:ih:iw*0.6836:0,scale=1080:1920,setsar=1"                  # stage side (Speaker on stage)
GUEST_HALF = "crop=iw*0.1758:ih*0.5556:iw*0.3034:ih*0.2606,scale=1080:1920,crop=1080:960:0:116,setsar=1"
SPEAKER_ZOOM, SPEAKER_EYE = "1.55", "0.32"                              # qa_reframe_v2: tracked, centers Speaker


def find_bin(name):
    c = sorted(glob.glob(f"/opt/homebrew/Cellar/ffmpeg-full/*/bin/{name}"), reverse=True)
    return c[0] if c else (shutil.which(name) or name)
FF, FFP = find_bin("ffmpeg"), find_bin("ffprobe")


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        sys.stderr.write("\nFAILED: " + " ".join(str(c) for c in cmd[:8]) + " ...\n" + (r.stderr or "")[-1600:] + "\n")
        raise SystemExit(1)
    return r


def dur_of(p):
    return float(run([FFP, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edl", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--format", default="qa", choices=["qa", "hotline", "dtc"])
    ap.add_argument("--sync", default=f"{SKILL}/references/qa_sync.json")
    ap.add_argument("--preset", default=f"{CAPS}/presets/spice.json")  # LOCKED caption style — caption-clips is SSOT
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--keep-temp", action="store_true")
    a = ap.parse_args()
    t0 = time.time()

    edl = json.load(open(a.edl)); segs = edl["segments"] if isinstance(edl, dict) else edl
    sync = json.load(open(a.sync))
    cam_dir = sync["cam_dir"]
    # role -> (camfile, offset)
    role2 = {}
    for base, role in sync["roles"].items():
        f = next((g for g in glob.glob(f"{cam_dir}/{base}.*")), None)
        role2[role] = (f, sync["offsets"][base])
    mics = [m if os.path.isabs(m) else f"{cam_dir}/{m}" for m in sync["mics"]]
    for s in segs:                                   # validate (split = composite of speaker+guest)
        if s["cam"] == "split":
            if not (role2.get("speaker", (None,))[0] and role2.get("guest", (None,))[0]):
                raise SystemExit("'split' cam needs both speaker + guest roles in qa_sync")
            continue
        if s["cam"] not in role2: raise SystemExit(f"unknown cam '{s['cam']}' (have {list(role2)} + split)")
        if not role2[s["cam"]][0]: raise SystemExit(f"cam file for role '{s['cam']}' not found in {cam_dir}")

    work = os.path.splitext(a.out)[0] + "_qabuild"; os.makedirs(work, exist_ok=True)
    print(f"qa_build: {len(segs)} segments, format={a.format}  (ffmpeg={os.path.basename(os.path.dirname(os.path.dirname(FF)))})", flush=True)

    # ---------- runs: merge contiguous segments. EDL boundaries are the diarized + tail-refined cut-list
    # (already carry the pre-word lead + the post-word tail into the pause), so cut EXACTLY there. ----------
    LEAD, TAIL = 0.0, 0.0
    runs = []
    for i, s in enumerate(segs):
        if runs and abs(s["mic_start"] - segs[runs[-1]["seg"][-1]]["mic_end"]) < 0.05: runs[-1]["seg"].append(i)
        else: runs.append({"seg": [i]})
    for r in runs:
        r["start"] = round(segs[r["seg"][0]]["mic_start"] - LEAD, 3)
        r["end"]   = round(segs[r["seg"][-1]]["mic_end"] + TAIL, 3)
        r["spk"]   = segs[r["seg"][0]]["speaker"]
    seg2run = {j: r for r in runs for j in r["seg"]}
    segdur = []                                  # (vs, ve) per segment = the ACTUAL clip timeline (= video, A/V in sync)
    for i, s in enumerate(segs):
        r = seg2run[i]
        segdur.append((r["start"] if i == r["seg"][0] else s["mic_start"],
                       r["end"] if i == r["seg"][-1] else s["mic_end"]))

    # ---------- AUDIO: ONE active-speaker mic per run (no amix -> no comb/echo). RAW hard cuts, 2 ms
    # de-click. Concat, normalize ONCE on the whole clip. NO per-run dynamics -- per-run dynaudnorm was
    # what ramped the gain at every cut and SOUNDED like a fade. ----------
    spk_mic = sync.get("speaker_mics", {})
    aparts = []
    for k, r in enumerate(runs):
        mic = spk_mic.get(r["spk"]) or mics[0]
        if not os.path.isabs(mic): mic = f"{cam_dir}/{mic}"
        d = r["end"] - r["start"]; out = f"{work}/a{k:03d}.wav"
        run([FF, "-y", "-loglevel", "error", "-ss", f"{r['start']:.3f}", "-i", mic, "-t", f"{d:.3f}",
             "-af", f"afade=t=in:st=0:d=0.002,afade=t=out:st={max(0, d-0.002):.3f}:d=0.002", "-ac", "2", "-ar", "48000", out])
        aparts.append(out)
    with open(f"{work}/al.txt", "w") as f:
        for p in aparts: f.write(f"file '{os.path.abspath(p)}'\n")
    run([FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", f"{work}/al.txt", "-c:a", "pcm_s16le", f"{work}/araw.wav"])
    p = subprocess.run([FF, "-i", f"{work}/araw.wav", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"], capture_output=True, text=True)
    m = json.loads(p.stderr[p.stderr.rindex("{"):p.stderr.rindex("}") + 1])
    af = (f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
          f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    run([FF, "-y", "-loglevel", "error", "-i", f"{work}/araw.wav", "-af", af, "-ar", "48000", "-ac", "2", f"{work}/amaster.wav"])
    print(f"  [audio] {len(runs)} runs, 1 mic/run HARD cuts (no per-run dynamics), normalized once -> -14 LUFS (raw {m['input_i']})  ({time.time()-t0:.0f}s)", flush=True)

    # ---------- SPEAKER reframe masters: merge speaker segments into contiguous source ranges, track each ----------
    af_cam, af_off = role2["speaker"]
    aseg = sorted([(s["mic_start"], s["mic_end"]) for s in segs if s["cam"] in ("speaker", "split")])
    ranges, PAD, GAP = [], 0.6, 20.0
    for s, e in aseg:
        if ranges and s - ranges[-1][1] <= GAP: ranges[-1][1] = max(ranges[-1][1], e)
        else: ranges.append([s, e])
    masters = []  # (r0_mic, r1_mic, path)
    if ranges: print(f"  [speaker] reframing {len(ranges)} range(s) ...", flush=True)
    for j, (r0, r1) in enumerate(ranges):
        r0 -= PAD; r1 += PAD; mp = f"{work}/speaker_{j}.mp4"
        if not (os.path.exists(mp) and os.path.getsize(mp) > 1e6):
            tmp = f"{work}/speaker_{j}_4k.mp4"
            run([FF, "-y", "-loglevel", "error", "-ss", f"{r0-af_off:.3f}", "-i", af_cam, "-t", f"{r1-r0:.3f}", "-an",
                 *encoder_args(3840, 2160, FF, tier="intermediate"), "-r", FPS, tmp])
            r = subprocess.run([sys.executable, REFRAME, tmp, mp, "--res", "1080", "--zoom", SPEAKER_ZOOM, "--eye-y", SPEAKER_EYE], capture_output=True, text=True)
            print("    " + (r.stdout.strip().splitlines()[-2] if r.stdout.strip() else r.stderr.strip()[-160:]), flush=True)
            if r.returncode: raise SystemExit(1)
            if not a.keep_temp: os.remove(tmp)
        masters.append((r0, r1, mp))

    def speaker_master(mic_start):
        for r0, r1, mp in masters:
            if r0 - 0.05 <= mic_start <= r1: return mp, r0
        raise SystemExit(f"no speaker master covers mic {mic_start}")

    # ---------- VIDEO: one clip per segment (the angle), concat ----------
    print(f"  [video] rendering {len(segs)} segments ...", flush=True); vids = []
    for i, s in enumerate(segs):
        vs, ve = segdur[i]                                      # snapped seam boundaries -> keep A/V in sync
        d = ve - vs; out = f"{work}/v{i:03d}.mp4"
        if s["cam"] == "speaker":
            mp, r0 = speaker_master(vs); ss = vs - r0
            run([FF, "-y", "-loglevel", "error", "-ss", f"{ss:.3f}", "-i", mp, "-t", f"{d:.3f}", "-an", "-vf", "setsar=1",
                 "-r", FPS, *encoder_args(OW, OH, FF, tier="delivery"), out])
        elif s["cam"] == "split":     # Speaker top / guest bottom 50-50 (Visual Guide split-screen)
            mp, r0 = speaker_master(vs); gcam, goff = role2["guest"]
            fc = ("[0:v]crop=1080:960:0:230,setsar=1[top];[1:v]" + GUEST_HALF +
                  "[bot];[top][bot]vstack,drawbox=x=0:y=957:w=1080:h=6:color=black@0.4:t=fill[v]")
            run([FF, "-y", "-loglevel", "error", "-ss", f"{vs-r0:.3f}", "-i", mp,
                 "-ss", f"{vs-goff:.3f}", "-i", gcam, "-t", f"{d:.3f}", "-an",
                 "-filter_complex", fc, "-map", "[v]", "-r", FPS, *encoder_args(OW, OH, FF, tier="delivery"), out])
        else:
            cam, off = role2[s["cam"]]; vf = GUEST_CROP if s["cam"] == "guest" else WIDE_CROP
            run([FF, "-y", "-loglevel", "error", "-ss", f"{vs-off:.3f}", "-i", cam, "-t", f"{d:.3f}", "-an", "-vf", vf,
                 "-r", FPS, *encoder_args(OW, OH, FF, tier="delivery"), out])
        vids.append(out)
    with open(f"{work}/vl.txt", "w") as f:
        for p in vids: f.write(f"file '{os.path.abspath(p)}'\n")
    r = subprocess.run([FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", f"{work}/vl.txt", "-c", "copy", f"{work}/vsilent.mp4"])
    if r.returncode:                                  # params differ -> re-encode concat
        c = [FF, "-y", "-loglevel", "error"]
        for p in vids: c += ["-i", p]
        c += ["-filter_complex", "".join(f"[{i}:v]" for i in range(len(vids))) + f"concat=n={len(vids)}:v=1:a=0[v]",
              "-map", "[v]", "-r", FPS, *encoder_args(OW, OH, FF, tier="delivery"), f"{work}/vsilent.mp4"]; run(c)

    base = f"{work}/base.mp4"
    run([FF, "-y", "-loglevel", "error", "-i", f"{work}/vsilent.mp4", "-i", f"{work}/amaster.wav", "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", "-shortest", base])
    print(f"  [mux] base built ({dur_of(base):.2f}s)  ({time.time()-t0:.0f}s)", flush=True)

    if a.no_captions:
        shutil.copy(base, a.out)
        print(f"\nDONE (no captions) -> {a.out}  {dur_of(a.out):.2f}s  total {time.time()-t0:.0f}s")
        if not a.keep_temp: shutil.rmtree(work, ignore_errors=True)
        return

    # ---------- CAPTIONS: transcribe -> lowercase -> money -> EDL-driven director -> spice -> HW burn ----------
    print("  [captions] transcribe + spice ...", flush=True)
    run([sys.executable, TRANSCRIBE, base, "--start", "0", "--end", f"{dur_of(base):.2f}", "--out", f"{work}/w_raw.json"])
    run([sys.executable, NORMALIZE, f"{work}/w_raw.json", f"{work}/w_norm.json"])     # lowercase (guide rule)
    run([sys.executable, SPICE_NORM, f"{work}/w_norm.json", f"{work}/w_spice.json"])  # $ / % / unit tokens
    words = json.load(open(f"{work}/w_spice.json"))["words"]
    # clip-time -> speaker, from the ACTUAL (snapped) segment durations
    cr, t = [], 0.0
    for i, s in enumerate(segs):
        d = segdur[i][1] - segdur[i][0]; cr.append((round(t, 3), round(t + d, 3), s["speaker"])); t += d
    json.dump(cr, open(os.path.splitext(a.out)[0] + "_clipmap.json", "w"))   # for qa_audit per-speaker checks
    def speaker_at(ct):
        for x0, x1, sp in cr:
            if x0 <= ct < x1: return sp
        return cr[-1][2]
    # WEIGHT emphasis + quotes from the LLM caption director (gold-bank few-shot ON by default);
    # COLOR per-word from MIC ENERGY + conversational turn-taking (shared with qa_assembly), so a
    # short turn-reply ("yes", "cool") is colored for the responder, not the camera-shot speaker.
    edl_guest, _ = resolve_speakers(words, segdur, segs, speaker_at, spk_mic, cam_dir, FF,
                                    debug=bool(os.environ.get("VIBE_DIAR_DEBUG")))
    director = {}
    _dr = subprocess.run([sys.executable, f"{CAPS}/scripts/caption_director.py", f"{work}/w_spice.json",
                          "--out", f"{work}/director_llm.json", "--context", "q&a workshop"],
                         capture_output=True, text=True)
    if _dr.returncode == 0 and os.path.exists(f"{work}/director_llm.json"):
        for k, v in json.load(open(f"{work}/director_llm.json")).get("words", {}).items():
            v = dict(v); v.pop("c", None)   # EDL owns COLOR; keep director's weight/size/italic (size axis ON 2026-06-11)
            director[k] = v
        print(f"  [captions] director: {len(director)} weighted words (gold-bank few-shot)", flush=True)
    else:
        print(f"  [captions] director LLM unavailable; weight-flat fallback", flush=True)
    for i in edl_guest:
        d = director.setdefault(str(i), {}); d["c"] = "guest"; d["i"] = True
    json.dump({"words": director, "voice_spans": []}, open(f"{work}/director.json", "w"))
    # LOCKED SHADOW: burn via generate_spice --burn (the two-layer gblur Premiere drop shadow —
    # the strong even dark halo), NOT the weak inline ASS shadow (\bord\blur in cc.ass) a subtitles=
    # burn would use. caption-clips is the SSOT for the shadow; always route delivery through --burn.
    cc_burned = f"{work}/cc_burned.mp4"
    run([sys.executable, GEN_SPICE, f"{work}/w_spice.json", "--preset", a.preset, "--style", f"{work}/director.json",
         "--out", f"{work}/cc.ass", "--burn", base, "--burn-out", cc_burned])
    _lint = f"{CAPS}/scripts/caption_lint.py"
    if os.path.exists(_lint):
        subprocess.run([sys.executable, _lint, f"{work}/cc.ass"])
    if not os.path.exists(cc_burned):
        sys.exit(f"caption burn failed — {cc_burned} missing")
    # Finishing pass on the gblur-shadowed burn (sync-safe — captions baked in): ~3-frame video lead
    # before audio (tpad clone + matching adelay, prevents swipe pop) + true-peak limiter to ~-6dB.
    run([FF, "-y", "-loglevel", "error", "-i", cc_burned,
         "-vf", "setsar=1,tpad=start_mode=clone:start_duration=0.1",
         "-af", "adelay=100|100,alimiter=limit=0.5:level=disabled",
         "-r", FPS, *encoder_args(OW, OH, FF, tier="delivery"),
         "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", a.out])
    g = sum(1 for v in director.values()); print(f"  [captions] {len(words)} words, {g} guest(yellow-italic) / {len(words)-g} speaker(white)", flush=True)

    print(f"\nDONE -> {a.out}  {dur_of(a.out):.2f}s  total {time.time()-t0:.0f}s")
    if not a.keep_temp: shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
