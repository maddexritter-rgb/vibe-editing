#!/usr/bin/env python3
"""qa_assembly — THE Speaker Q&A ASSEMBLY-CUT standard (locked 2026-06-06).

EDL-driven multi-cam assembly matching the reference editor's house style: split-screen open (Speaker top /
guest bottom + soft seam drop-shadow), then cut between CAMERAS for variety — NEVER zoom
in/out within a camera. Each camera holds ONE consistent size:
  cam "speaker"       = C2092 tracked, 3/4 head-to-thigh (zoom 1.6)        [Y-LOCK + box-center]
  cam "guest"      = C2161 tracked, chest/waist-up (zoom 1.4)           [Y-LOCK + box-center]
  cam "guest_wide" = C2118 SIDE cam, punched in on the standing guest   [static crop]
  cam "split"      = Speaker top (zoom 1.4) / guest bottom + seam shadow
  cam "wide"       = C2118 stage side  |  *_close suffix = a tighter variant (use sparingly)
Reframe is Y-LOCKED (x-tracking only) + centers the face BOX (not the nose tip).
PER-GUEST: the guest CROPS/ROIs (GUEST_HALF, GUEST_WIDE_CROP, GUEST_ROI, SPEAKER_ROI) encode
each guest's seat position -> override per clip in a job wrapper (monkeypatch these globals).
The ZOOMS + drop-shadow + Y-lock logic ARE the locked standard. Constants below = Guest's
"ExampleClip" reference exemplar.

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
import os, sys, json, glob, shutil, subprocess, argparse, time, hashlib

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # .../edit
CAPS  = _acq("caption-clips")
SHARED= VIBE_SHARED
sys.path.insert(0, SHARED)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # so sibling helpers import under importlib too
from fast_encode import encoder_args
from speaker_diarize import resolve_speakers   # per-word Speaker/guest (mic energy + turn-taking)

REFRAME   = f"{SKILL}/scripts/qa_reframe_v2.py"                   # Y-LOCK + box-center reframe (the assembly standard)
GUEST_PANEL = _acq("horizontal-to-vertical/scripts/guest_panel_render.py")  # DYNAMIC target-framing guest split panel (2026-06-17): face=target_face_h/target_face_y solved per-frame; kills the per-guest "too big/too low" bug.
SHADOW    = f"{SKILL}/assets/seam_shadow.png"                     # split seam drop-shadow
GEN_SPICE = f"{CAPS}/scripts/generate_spice.py"
# Per-angle caption-Y track. layout_analyze lives in the caption-app worker (shared by the
# app and /edit — caption-clips is the caption SSOT; this is the layout/positioning tool).
LAYOUT_ANALYZE = _acqv("caption-app/worker/layout_analyze.py")
NORMALIZE = f"{CAPS}/scripts/normalize_simple.py"
SPICE_NORM= f"{CAPS}/scripts/spice_normalize.py"
TRANSCRIBE= f"{CAPS}/scripts/transcribe_lv3.py"
FONTSDIR  = f"{CAPS}/fonts/free_font"

# Output resolution: default 1080x1920 (playback proxy). Set VIBE_QA_RES=2160 for a TRUE 4K
# render of the SAME cut (reframe masters are rendered at 4K either way; this keeps them at 4K
# instead of downscaling). All crops below use iw*/ih* fractions (res-independent) — only the
# scale targets + split-seam geometry scale with OW/OH, so the 4K output is the 1080 cut at 2x.
_RES = int(os.environ.get("VIBE_QA_RES", "1080"))
OW, OH = (2160, 3840) if _RES >= 2160 else (1080, 1920)
FPS = "30000/1001"
FADE = 0.030
WIDE_CROP  = f"crop=iw*0.3164:ih:iw*0.6836:0,scale={OW}:{OH},setsar=1"   # wide cam (unused in Guest EDL)
GUEST_HALF = f"crop=iw*0.4219:ih*0.6667:iw*0.1742:ih*0.1759,scale={OW}:{OH//2},setsar=1"  # split bottom (static, head w/ headroom)
GUEST_WIDE_CROP = f"crop=ih*0.28*9/16:ih*0.28:iw*0.1388:ih*0.406,scale={OW}:{OH},setsar=1"  # C2118 side cam, PUNCHED IN on the guest (~3x; soft but fills frame)
# v2 reframe params (Y-locked, box-centered). Per-guest framing differs -> these are Guest's.
SPEAKER_ZOOM,   SPEAKER_EYE,   SPEAKER_ROI  = "1.6",  "0.18", "0.05 0.05 0.82 0.55"  # Speaker = ONE consistent 3/4 (head-to-thigh); no zoom in/out
SPLIT_ZOOM,  SPLIT_EYE             = "1.4",  "0.22"                         # Speaker in the split top
GUEST_ZOOM,  GUEST_EYE, GUEST_ROI  = "1.4",  "0.24", "0.25 0.15 0.58 0.48"  # guest = ONE consistent chest/waist-up (roomy)
GUEST_SPLIT_ZOOM, GUEST_SPLIT_EYE  = "1.1",  "0.22"   # guest in the SPLIT panel: face-tracked, Y-locked upper-third.
# ^ MEASURE, don't guess: at zoom Z the guest's face ≈ (face%@Z=1)·Z of the panel. On a WIDE room cam a "small"
#   subject is still large once the ROI punches in — zoom 2.0 made the guest's face 57% of the panel (a giant
#   close-up) vs the host's ~21%. 1.1 lands ~32% = head-and-shoulders comparable to the host. eye 0.22 matches the
#   Speaker split-top. Override per camera via sync["guest_split"]={"zoom","eye","roi"}; pick zoom so the guest's face%
#   ≈ the host's (~20-35%) — verify with detect_face_dense on both panels. roi GUARDS against audience-face lock-on.

# ---- HOUSE COLOR GRADE (Speaker Q&A SF Visual Guide: guest=cool/blue, Speaker=bright/saturated). This footage is
# Rec709 (Sony XAVC bt709) -> a moderate CURVE grade, NOT the Buttery log->709 LUTs (those expect S-Log input
# and DOUBLE-apply the transform on baked 709 -> crushed/oversaturated; verified). "Premiere-punch", not heavy.
# Grade follows who's ON SCREEN (cam), not the speaker; split-screen grades each half independently.
GRADE       = True
SPEAKER_GRADE  = "eq=brightness=0.03:contrast=1.06:saturation=1.18:gamma=1.04,colortemperature=temperature=5800:mix=0.35"
GUEST_GRADE = "eq=brightness=0.02:contrast=1.05:saturation=1.05:gamma=1.02,colortemperature=temperature=7600:mix=0.34"
def grade_for(cam):
    if not GRADE: return ""
    return GUEST_GRADE if cam.startswith("guest") else SPEAKER_GRADE   # speaker / wide(stage) -> SPEAKER; split handled per-half

# ---- Clean-audio chain. GENTLE by design: the SF afftdn DENOISER + 8kHz air boost OVER-PROCESSED Speaker's
# quiet-but-clean lav — the ~+18dB loudnorm boost amplified the denoiser's musical-noise + hiss into a hazy,
# smeared "sounds completely shit" tone (Operator 2026-06-07; verified on a spectrogram, the floor was pumped up).
# Fix: highpass + de-mud + gentle presence + gentle compression. NO afftdn, NO air boost -> floor stays DARK.
CLEAN_AUDIO    = True
CLEAN_AUDIO_AF = ("highpass=f=80,equalizer=f=200:t=q:w=1:g=-2,equalizer=f=3000:t=q:w=1.5:g=2,"
                  "acompressor=threshold=-22dB:ratio=2.5:attack=8:release=160:makeup=2")


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
    ap.add_argument("--preset", default=f"{CAPS}/presets/spice.json")  # LOCKED caption style (shadow/Medium-base/font/position) — caption-clips is SSOT
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--keep-temp", action="store_true")
    ap.add_argument("--music", default=None, help="path to a music bed (mixed UNDER the voice, loudnorm bed, HARD out)")
    ap.add_argument("--music-ss", type=float, default=0.0, help="seek into the music track (skip intro)")
    ap.add_argument("--music-delay", type=float, default=0.0, help="delay music start in the OUTPUT timeline (seconds). Use to silence music during the split-screen Q opener and have it kick in when Speaker starts answering (per Operator review 2026-06-16: 'music should start right here' after the question).")
    ap.add_argument("--music-level", type=float, default=-28.0, help="loudnorm I for the music bed (under the -14 voice)")
    ap.add_argument("--dump-map", action="store_true", help="print the clip-time -> segment/cam/mic map (for review-note translation) and exit")
    ap.add_argument("--corrections", default=None, help="JSON file of {heard: burned} word/phrase fixes applied to the transcribed words before normalization. For Whisper mishearings on the rendered audio (e.g. 'trading' should burn as 'draining').")
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
    # --- PER-SPEAKER MICS: each speaker is mixed on their OWN clean lav (active full, off-speaker ducked).
    # CRITICAL for a multi-speaker EDL: without this the mix silently falls back to mics[0] for EVERYONE,
    # so e.g. the guest's question plays off SPEAKER's lav as faint room bleed, then loudnorm amplifies that
    # ~+18-30dB into hiss (the "terrible audio" bug, 2026-06-14). Infer from filenames if qa_sync omitted it.
    spk_mic = {k: (v if os.path.isabs(v) else f"{cam_dir}/{v}") for k, v in sync.get("speaker_mics", {}).items()}
    if not spk_mic:
        for m in mics:
            b = os.path.basename(m).upper()
            if any(k in b for k in ("SPEAKER", "HOST")) and "speaker" not in spk_mic: spk_mic["speaker"] = m
            if any(k in b for k in ("GUEST", "CALLER", "ATTENDEE")) and "guest" not in spk_mic: spk_mic["guest"] = m
    _edl_spk = {s.get("speaker") for s in segs if s.get("speaker")}
    if len(_edl_spk) > 1 and not all(spk_mic.get(s) for s in _edl_spk):
        print(f"  [audio][WARN] multi-speaker EDL {sorted(_edl_spk)} but speaker_mics incomplete "
              f"({sorted(spk_mic)}); missing speakers fall back to mics[0] (WRONG mic = bleed/hiss). "
              f"Add speaker_mics to qa_sync.json.", flush=True)
    for s in segs:                                   # validate (split = composite of speaker+guest)
        if s["cam"] == "split":
            if not (role2.get("speaker", (None,))[0] and role2.get("guest", (None,))[0]):
                raise SystemExit("'split' cam needs both speaker + guest roles in qa_sync")
            continue
        base = ("wide" if s["cam"] == "guest_wide" else "speaker" if s["cam"].startswith("speaker")
                else "guest" if s["cam"].startswith("guest") else s["cam"])
        if base not in role2: raise SystemExit(f"unknown cam '{s['cam']}' (have {list(role2)} + *_close + guest_wide + split)")
        if not role2[base][0]: raise SystemExit(f"cam file for role '{base}' not found in {cam_dir}")

    work = os.path.splitext(a.out)[0] + "_qabuild"; os.makedirs(work, exist_ok=True)
    # Content-addressed reframe-master cache, SHARED across clips + revisions in this job dir.
    # Masters are keyed by cam+range+framing+reframer-version (not by output filename), so an EDL
    # revision only re-tracks the merged range(s) that actually CHANGED — unchanged ranges reuse
    # instantly. Persists across runs (not wiped with `work`). Clear it to force a clean rebuild.
    MCACHE = os.path.join(os.path.dirname(os.path.abspath(a.out)), "_qa_mastercache"); os.makedirs(MCACHE, exist_ok=True)
    print(f"qa_build: {len(segs)} segments, format={a.format}  (ffmpeg={os.path.basename(os.path.dirname(os.path.dirname(FF)))})", flush=True)

    # ---------- runs: merge contiguous segments. EDL boundaries are the diarized + tail-refined cut-list
    # (already carry the pre-word lead + the post-word tail into the pause), so cut EXACTLY there. ----------
    LEAD, TAIL = 0.0, 0.0
    END_TAIL_PAD = 0.12   # after snapping a run-END to the post-word silence, keep this much more so a soft
                          # trailing consonant ("opportunity" -> "-ty") isn't clipped (Operator note 2026-06-07).
    runs = []
    for i, s in enumerate(segs):
        if runs and abs(s["mic_start"] - segs[runs[-1]["seg"][-1]]["mic_end"]) < 0.05: runs[-1]["seg"].append(i)
        else: runs.append({"seg": [i]})
    for r in runs:
        r["start"] = round(segs[r["seg"][0]]["mic_start"] - LEAD, 3)
        r["end"]   = round(segs[r["seg"][-1]]["mic_end"] + TAIL, 3)
        r["spk"]   = segs[r["seg"][0]]["speaker"]

    # ---------- SNAP run boundaries to true acoustic word edges (precision-cut standard, Operator note
    # 2026-06-06: "cutting words in half — cut the END of a word"). Measure a per-speaker floor, then
    # silencedetect the speaker mic at each boundary and snap START back to the word onset / END forward
    # to the silence AFTER the last word. Bounded +/-0.5s; falls back to the EDL time if no edge found. ----------
    # spk_mic inferred once after the mics-def above (per-speaker clean lavs)
    def _floor(mic, t0, t1):
        e = subprocess.run([FF, "-ss", f"{max(0,t0):.3f}", "-i", mic, "-t", f"{max(1,t1-t0):.3f}",
            "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
        for ln in e.splitlines():
            if "mean_volume" in ln: return float(ln.split("mean_volume:")[1].split("dB")[0]) - 8.0
        return -38.0
    def _sils(mic, t0, t1, fl):
        e = subprocess.run([FF, "-ss", f"{max(0,t0):.3f}", "-i", mic, "-t", f"{t1-t0:.3f}",
            "-af", f"silencedetect=noise={fl:.1f}dB:d=0.05", "-f", "null", "-"], capture_output=True, text=True).stderr
        sils, cur = [], None
        for ln in e.splitlines():
            if "silence_start:" in ln: cur = float(ln.split("silence_start:")[1])
            elif "silence_end:" in ln and cur is not None:
                sils.append((t0+cur, t0+float(ln.split("silence_end:")[1].split("|")[0]))); cur = None
        return sils
    if spk_mic and runs:
        lo, hi = min(r["start"] for r in runs), max(r["end"] for r in runs)
        flo = {sp: _floor((m if os.path.isabs(m) else f"{cam_dir}/{m}"), lo, hi) for sp, m in spk_mic.items()}
        for ri, r in enumerate(runs):
            m = spk_mic.get(r["spk"]) or mics[0]; m = m if os.path.isabs(m) else f"{cam_dir}/{m}"
            es, ee = r["start"], r["end"]
            sl = _sils(m, es-0.6, ee+0.6, flo.get(r["spk"], -38.0))
            cs = [se for _, se in sl if abs(se - es) <= 0.5]   # silence_end = speech onset
            ce = [ss for ss, _ in sl if abs(ss - ee) <= 0.5]   # silence_start = speech offset
            back = [se for se in cs if es - 0.16 <= se <= es + 0.08]   # SMALL back-pull only — catch a slightly-late onset
            fwd  = [ss for ss in ce if ss >= ee - 0.08]
            # START: the FIRST run (the hook) NEVER snaps earlier than its EDL start (else it grabs a greeting tail);
            # it only lands FORWARD on a clean onset. Later runs may pull back a LITTLE to a word onset, but NEVER far
            # enough to re-include a deliberately-cut lead-in (Operator 2026-06-07: the "[pause] and so" cut was being
            # undone because the snap pulled back 0.4s to the prior phrase). If no onset in the tight window, KEEP the
            # EDL start (it's the transcript-exact word boundary).
            if ri == 0:
                fwd_on = [se for se in cs if se >= es - 0.02]
                if fwd_on: r["start"] = round(min(fwd_on, key=lambda x: abs(x - es)), 3)
            elif back:  r["start"] = round(max(back), 3)
            # END: snap to the post-word silence, then keep END_TAIL_PAD more so a soft trailing consonant
            # isn't shaved (Operator note 008). A run-end always precedes a deletion/clip-end, so the pad is safe.
            if fwd:   r["end"] = round(min(fwd), 3)
            elif ce:  r["end"] = round(min(ce, key=lambda x: abs(x - ee)), 3)
            r["end"] = round(r["end"] + END_TAIL_PAD, 3)

    seg2run = {j: r for r in runs for j in r["seg"]}
    segdur = []                                  # (vs, ve) per segment = the ACTUAL clip timeline (= video, A/V in sync)
    for i, s in enumerate(segs):
        r = seg2run[i]
        segdur.append((r["start"] if i == r["seg"][0] else s["mic_start"],
                       r["end"] if i == r["seg"][-1] else s["mic_end"]))

    if a.dump_map:                               # review-note translation: clip-time <-> segment/cam/mic (snapped)
        t = 0.0
        print(f"# clip-time -> segment map (snapped) for {os.path.basename(a.out)}")
        for i, s in enumerate(segs):
            vs, ve = segdur[i]; d = ve - vs
            mm = lambda x: f"{int(x//60)}:{x%60:05.2f}"
            print(f"seg{i:02d}  clip[{mm(t)}-{mm(t+d)}]  {s['cam']:10} spk={s['speaker']:5}  mic[{vs:.2f}-{ve:.2f}]  dur={d:.2f}")
            t += d
        print(f"# total {t:.2f}s")
        return

    # ---------- AUDIO: mix BOTH conversational mics per run so the OFF-cam speaker's reactions ("yes",
    # laughs) stay audible no matter which camera is shown (Operator note 2026-06-06: guest "yes" was being
    # dropped when it cut to Speaker). Close lav/handheld mics -> low cross-bleed; highpass tames rumble.
    # RAW hard cuts + 2 ms de-click. Concat, normalize ONCE on the whole clip. ----------
    # spk_mic inferred once after the mics-def above (per-speaker clean lavs)
    conv_pairs = [((spk_mic[s] if os.path.isabs(spk_mic[s]) else f"{cam_dir}/{spk_mic[s]}"), s) for s in ("speaker", "guest") if spk_mic.get(s)] or [(mics[0], None)]
    conv = [m for m, _ in conv_pairs]; conv_spk = [s for _, s in conv_pairs]
    # 🔒 AUDIO SELF-CHECK (always-on): print the per-speaker mic mapping so it's auditable EVERY build, and WARN on
    # the wrong-mic/bleed bug (a speaker mixed on a mic that isn't theirs, or a 1-mic mix on a multi-speaker EDL ->
    # the off-speaker plays as room bleed amplified into hiss). Audio MUST always be double-checked.
    _NAMETOK = {"speaker": ("SPEAKER", "HOST"), "guest": ("GUEST", "CALLER", "ATTENDEE")}
    for _m, _sp in conv_pairs:
        print(f"  [audio][check] {(_sp or 'mics[0]'):5} mixed on -> {os.path.basename(_m)}", flush=True)
    if len(conv) < 2 and len(_edl_spk) > 1:
        print(f"  [audio][WARN] only 1 mic for a {len(_edl_spk)}-speaker EDL — EVERY speaker uses the same mic (bleed/hiss). Set speaker_mics in qa_sync.json.", flush=True)
    for _sp in _edl_spk:
        _am = spk_mic.get(_sp)
        if _am and not any(tok in os.path.basename(_am).upper() for tok in _NAMETOK.get(_sp, ())):
            print(f"  [audio][WARN] speaker '{_sp}' is mixed on '{os.path.basename(_am)}' — doesn't look like {_sp}'s mic. Verify it's not the WRONG mic (bleed).", flush=True)
    aparts = []
    for k, r in enumerate(runs):
        d = r["end"] - r["start"]; out = f"{work}/a{k:03d}.wav"
        cmd = [FF, "-y", "-loglevel", "error"]
        for mic in conv: cmd += ["-ss", f"{r['start']:.3f}", "-i", mic]
        pre = "".join(f"[{i}:a]highpass=f=70[m{i}];" for i in range(len(conv)))
        if len(conv) > 1:
            # 🛑 2026-06-16 Frame review on Guest, comment @5.75s: "guest cam cuts out here... you used the
            # wrong mic. You're supposed to have the guest audio track and Speaker audio track on the entire time."
            # PREVIOUS behavior: ACTIVE speaker's mic at 1.0, OFF mic ducked to 0.30. When the active speaker
            # was QUIET on their own mic (e.g. Guest holding the wireless loosely at source 5903), the ducked
            # off-mic contributed too little to fill in — output sounded like a mic cut.
            # NEW behavior: BOTH mics at 1.0 always. The amix normalize=0 + 2-pass loudnorm-to-14 handles level
            # balance; if the off-mic is genuinely dead (no bleed), it contributes nothing, no harm. The wins
            # for spoken-into-by-soft-talker cases more than make up for occasional room-tone bleed.
            wts = " ".join("1.0" for _ in range(len(conv)))
            mix = "".join(f"[m{i}]" for i in range(len(conv))) + f"amix=inputs={len(conv)}:weights={wts}:normalize=0[mx];"
        else:
            mix = "[m0]anull[mx];"
        fc = pre + mix + f"[mx]afade=t=in:st=0:d=0.002,afade=t=out:st={max(0, d-0.002):.3f}:d=0.002[out]"
        cmd += ["-t", f"{d:.3f}", "-filter_complex", fc, "-map", "[out]", "-ac", "2", "-ar", "48000", out]
        run(cmd)
        aparts.append(out)
    with open(f"{work}/al.txt", "w") as f:
        for p in aparts: f.write(f"file '{os.path.abspath(p)}'\n")
    run([FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", f"{work}/al.txt", "-c:a", "pcm_s16le", f"{work}/araw.wav"])
    # 🔒 AUDIO SELF-CHECK (always-on): the wrong-mic/bleed bug shows up as a NARROW dynamic range — the noise floor
    # rides up close to the speech because the active voice is faint on the wrong mic. A clean per-speaker lav has a
    # wide range (loud speech, quiet floor). WARN if the range is narrow.
    try:
        import re as _re
        _r = subprocess.run([FF, "-hide_banner", "-i", f"{work}/araw.wav", "-af",
                             "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
                             "-f", "null", "-"], capture_output=True, text=True)
        _vals = [float(x) for x in _re.findall(r"RMS_level=(-?\d+\.?\d*)", _r.stderr)]
        if len(_vals) > 4:
            _floor, _peak = min(_vals), max(_vals); _range = _peak - _floor
            print(f"  [audio][check] voice dynamic range = {_range:.0f} dB (floor {_floor:.0f} / peak {_peak:.0f}); clean lav ≳35 dB", flush=True)
            if _range < 25:
                print(f"  [audio][WARN] narrow dynamic range ({_range:.0f} dB) — the voice may be on the WRONG mic (room bleed). Check speaker_mics.", flush=True)
    except Exception:
        pass
    # SF "Clean Audio Preset" voice chain (DeNoise -> presence/air EQ -> compressor -> hard limiter), applied to the
    # spliced dialogue BEFORE the final loudnorm. Mirrors the Premiere preset's signal path (the .prfpset values are
    # base64-blobbed); tuned gentle for already-clean lav/board mics. Toggle via CLEAN_AUDIO.
    src = f"{work}/araw.wav"
    if CLEAN_AUDIO:
        run([FF, "-y", "-loglevel", "error", "-i", f"{work}/araw.wav", "-af", CLEAN_AUDIO_AF, "-ar", "48000", "-ac", "2", f"{work}/araw_clean.wav"])
        src = f"{work}/araw_clean.wav"
    p = subprocess.run([FF, "-i", src, "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"], capture_output=True, text=True)
    m = json.loads(p.stderr[p.stderr.rindex("{"):p.stderr.rindex("}") + 1])
    af = (f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
          f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    run([FF, "-y", "-loglevel", "error", "-i", src, "-af", af, "-ar", "48000", "-ac", "2", f"{work}/amaster.wav"])
    print(f"  [audio] {len(runs)} runs, {len(conv)}-mic conv mix/run (off-cam reactions kept) + word-snapped cuts"
          f"{' + clean-audio' if CLEAN_AUDIO else ''}, normalized once -> -14 LUFS (raw {m['input_i']})  ({time.time()-t0:.0f}s)", flush=True)

    # ---------- reframe masters (v2: Y-LOCKED + box-centered; guest is now TRACKED too) ----------
    # SINGLE-PASS PER-ROLE (2026-06-14): per the LOCKED render/stages/reframe.py architecture
    # ("per-segment-split-then-concat is the DEPRECATED approach — it causes a visible framing
    # wobble at every seam"), build_masters now concats ALL ranges per role into ONE input and
    # calls qa_reframe_v2 ONCE with --cut-frames at the seam positions. The tracker resets per
    # seam but the smoothing is continuous across the whole role — no jump at concat boundaries.
    # Set VIBE_QA_PER_RANGE=1 to revert to the legacy per-range path.
    def build_masters(role, cams_set, zoom, eye, roi, tag, lock_y=True, panel_mode=False, tfh=0.34, tfy=0.34):
        # lock_y=False enables X+Y per-frame tracking (default for guest split panel
        # since 2026-06-16 Frame review on Guest: "we need to add X and Y tracking for the
        # guest when it's split screen and only when it's split screen. The camera tends
        # to move a lot for the guest cam on these Q&As."). Speaker/single keep Y-LOCK.
        # panel_mode=True (2026-06-17): build the guest panel via guest_panel_render.py —
        # DYNAMIC target-framing. Output is the PANEL directly (OW x OH//2), face solved to
        # tfh/tfy per-frame from the detected face size (no fixed zoom). Validated across all
        # 9 session guests (face_y locked 31-33%; uniform chest-up). Set guest_split.mode="zoom"
        # in qa_sync to fall back to the legacy fixed-zoom reframe.
        cam, off = role2[role]
        segr = sorted([(s["mic_start"], s["mic_end"]) for s in segs if s["cam"] in cams_set])
        rngs, PAD, GAP = [], 0.6, 20.0
        for s, e in segr:
            if rngs and s - rngs[-1][1] <= GAP: rngs[-1][1] = max(rngs[-1][1], e)
            else: rngs.append([s, e])
        out = []
        if not rngs: return out
        # panel_mode (target-framed guest panel) MUST use the single-pass guest_panel_render path —
        # the legacy per-range path calls qa_reframe_v2 (fixed-zoom) and IGNORES panel_mode, so a clip
        # with a single split range silently fell back to a zoom-1.0 reframe (StageQA RegretTest: guest
        # face landed ~68% of master / 59% of panel, unstable). Never go legacy when panel_mode. (2026-06-17)
        legacy = ((os.environ.get("VIBE_QA_PER_RANGE") == "1") or (len(rngs) <= 1)) and not panel_mode
        if legacy:
            print(f"  [{tag}] reframing {len(rngs)} range(s) zoom={zoom} y-lock {'(legacy per-range)' if not (len(rngs)<=1) else ''}...", flush=True)
            for j, (r0, r1) in enumerate(rngs):
                r0 -= PAD; r1 += PAD
                _key = hashlib.md5(f"{cam}|{off:.3f}|{r0:.3f}|{r1:.3f}|{zoom}|{eye}|{roi}|{FPS}|{OW}x{OH}|{os.path.getmtime(REFRAME):.0f}".encode()).hexdigest()[:16]
                mp = f"{MCACHE}/{tag}_{_key}.mp4"
                if not (os.path.exists(mp) and os.path.getsize(mp) > 1e6):
                    tmp = f"{MCACHE}/{tag}_{_key}_4k.mp4"
                    run([FF, "-y", "-loglevel", "error", "-ss", f"{r0-off:.3f}", "-i", cam, "-t", f"{r1-r0:.3f}", "-an",
                         *encoder_args(3840, 2160, FF, tier="intermediate"), "-r", FPS, tmp])
                    _rcmd = [sys.executable, REFRAME, tmp, mp, "--res", ("4k" if OW >= 2160 else "1080"), "--zoom", str(zoom),
                         "--eye-y", str(eye), "--xcenter", "box", "--roi", *roi.split()]
                    if lock_y: _rcmd.insert(_rcmd.index("--xcenter"), "--lock-y")
                    rr = subprocess.run(_rcmd, capture_output=True, text=True)
                    print("    " + (rr.stdout.strip().splitlines()[-1] if rr.stdout.strip() else rr.stderr.strip()[-160:]), flush=True)
                    if rr.returncode: raise SystemExit(1)
                    if os.path.exists(tmp): os.remove(tmp)
                out.append((r0, r1, mp, r0))   # r0_eff = r0 (legacy: master_offset_at_range_start = 0)
            return out
        # ---- SINGLE-PASS PATH ----
        print(f"  [{tag}] reframing {len(rngs)} range(s) zoom={zoom} {'y-lock' if lock_y else 'X+Y-track'} SINGLE-PASS (concat + --cut-frames; no inter-range wobble)", flush=True)
        # Stable cache key over all ranges + params
        ranges_str = ",".join(f"{r0-PAD:.3f}-{r1+PAD:.3f}" for r0, r1 in rngs)
        if panel_mode:
            _key = hashlib.md5(f"gp|{cam}|{off:.3f}|{ranges_str}|tfh{tfh}|tfy{tfy}|{roi}|{FPS}|{OW}x{OH//2}|{os.path.getmtime(GUEST_PANEL):.0f}".encode()).hexdigest()[:16]
        else:
            _key = hashlib.md5(f"sp|{cam}|{off:.3f}|{ranges_str}|{zoom}|{eye}|{roi}|{FPS}|{OW}x{OH}|{os.path.getmtime(REFRAME):.0f}".encode()).hexdigest()[:16]
        mp = f"{MCACHE}/{tag}_sp_{_key}.mp4"
        # Compute cumulative offsets for each range (master timeline starts at 0)
        padded = [(r0 - PAD, r1 + PAD) for r0, r1 in rngs]
        cum, cum_offsets = 0.0, []
        for r0p, r1p in padded:
            cum_offsets.append(cum)
            cum += (r1p - r0p)
        # FPS parsing
        if "/" in FPS:
            fps_num, fps_den = [int(x) for x in FPS.split("/")]
            fps_val = fps_num / fps_den
        else:
            fps_val = float(FPS)
        cut_frames = [int(round(c * fps_val)) for c in cum_offsets[1:]]   # boundaries (after range 0)
        if not (os.path.exists(mp) and os.path.getsize(mp) > 1e6):
            # Extract each range from source
            tmps = []
            for j, (r0p, r1p) in enumerate(padded):
                tmp_ext = f"{MCACHE}/{tag}_sp_{_key}_e{j:02d}.mp4"
                run([FF, "-y", "-loglevel", "error", "-ss", f"{r0p-off:.3f}", "-i", cam, "-t", f"{r1p-r0p:.3f}", "-an",
                     *encoder_args(3840, 2160, FF, tier="intermediate"), "-r", FPS, tmp_ext])
                tmps.append(tmp_ext)
            # Concat into one input
            list_path = f"{MCACHE}/{tag}_sp_{_key}_list.txt"
            with open(list_path, "w") as f:
                for t in tmps:
                    f.write(f"file '{os.path.abspath(t)}'\n")
            concat_in = f"{MCACHE}/{tag}_sp_{_key}_in.mp4"
            run([FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", list_path,
                 "-c", "copy", concat_in])
            # Single-pass reframe with cut-frames at seams
            if panel_mode:
                # DYNAMIC target-framing guest panel (2026-06-17): output is the PANEL (OW x OH//2).
                cmd = [sys.executable, GUEST_PANEL, concat_in, mp, "--target-face-h", str(tfh), "--target-face-y", str(tfy),
                       "--roi", roi, "--panel-w", str(OW), "--panel-h", str(OH // 2), "--fps", FPS]
            else:
                cmd = [sys.executable, REFRAME, concat_in, mp, "--res", ("4k" if OW >= 2160 else "1080"),
                       "--zoom", str(zoom), "--eye-y", str(eye), "--xcenter", "box", "--roi", *roi.split()]
                if lock_y: cmd.insert(cmd.index("--xcenter"), "--lock-y")
            if cut_frames:
                cmd += ["--cut-frames", ",".join(str(c) for c in cut_frames)]
            rr = subprocess.run(cmd, capture_output=True, text=True)
            print("    " + (rr.stdout.strip().splitlines()[-1] if rr.stdout.strip() else rr.stderr.strip()[-160:]), flush=True)
            if rr.returncode: raise SystemExit(1)
            for t in tmps + [list_path, concat_in]:
                try:
                    if os.path.exists(t): os.remove(t)
                except OSError: pass
        # Build out: r0_eff such that `vs - r0_eff = offset_in_master`
        # offset_in_master = (vs - r0p) + cum_offset_for_this_range
        # ⇒ r0_eff = r0p - cum_offset_for_this_range
        for j, (r0p, r1p) in enumerate(padded):
            r0_eff = r0p - cum_offsets[j]
            out.append((r0p, r1p, mp, r0_eff))
        return out

    def lookup(masters, ms, what):
        # masters: list of (r0_src, r1_src, master_path, r0_eff)
        for tup in masters:
            r0, r1, mp = tup[0], tup[1], tup[2]
            r0_eff = tup[3] if len(tup) >= 4 else r0
            if r0 - 0.05 <= ms <= r1: return mp, r0_eff
        raise SystemExit(f"no {what} master covers mic {ms}")

    ANGLE = {  # EDL cam value -> (role, zoom, eye, roi). ONE consistent size per camera (no zoom in/out).
        "speaker":  ("speaker",  SPEAKER_ZOOM,  SPEAKER_EYE,  SPEAKER_ROI),   # 3/4 head-to-thigh
        "guest": ("guest", GUEST_ZOOM, GUEST_EYE, GUEST_ROI),  # chest/waist-up
    }
    masters = {}
    for ct, (role, z, e, roi) in ANGLE.items():
        if any(s["cam"] == ct for s in segs):
            masters[ct] = build_masters(role, (ct,), z, e, roi, ct)
    split_m = build_masters("speaker", ("split",), SPLIT_ZOOM, SPLIT_EYE, SPEAKER_ROI, "speakersplit") if any(s["cam"] == "split" for s in segs) else []
    # GUEST split panel = a FACE-TRACKED reframe master (Y-locked, box-centered), same as Speaker's panel — NOT a
    # static crop (the old GUEST_HALF mis-framed any guest who didn't stand exactly where the original setup did).
    # zoom/eye/roi overridable per camera via sync["guest_split"]; the roi GUARDS against audience-face lock-on.
    _gs = sync.get("guest_split", {})
    _gsz, _gse, _gsroi = str(_gs.get("zoom", GUEST_SPLIT_ZOOM)), str(_gs.get("eye", GUEST_SPLIT_EYE)), _gs.get("roi", GUEST_ROI)
    # GUEST SPLIT PANEL: X+Y tracking (lock_y=False) per 2026-06-16 Frame review on Guest.
    # Reason: the guest cam moves a lot during Tier1 Q&As — a Y-locked panel goes off-frame.
    # Override via qa_sync.guest_split.lock_y if a specific session genuinely needs Y-lock.
    _gs_locky = bool(_gs.get("lock_y", False))
    # 🎯 GUEST PANEL MODE (2026-06-17, DEFAULT = "target"): dynamic target-framing — the guest's face is
    # solved to guest_split.target_face_h / target_face_y of the panel PER-FRAME (no fixed zoom). Validated
    # across all 9 Tier1 guests: uniform chest-up, face_y locked 31-33%, zero per-guest tuning. This kills the
    # "guest too big/too low/too high" bug class. Set guest_split.mode="zoom" for the legacy fixed-zoom path.
    _gs_mode = _gs.get("mode", "target")
    _gs_tfh, _gs_tfy = float(_gs.get("target_face_h", 0.34)), float(_gs.get("target_face_y", 0.34))
    _gs_panel = (_gs_mode == "target")
    if any(s["cam"] == "split" for s in segs):
        if _gs_panel:
            guestsplit_m = build_masters("guest", ("split",), 1.0, 0.0, _gsroi, "guestpanel",
                                         panel_mode=True, tfh=_gs_tfh, tfy=_gs_tfy)
        else:
            guestsplit_m = build_masters("guest", ("split",), _gsz, _gse, _gsroi, "guestsplit", lock_y=_gs_locky)
    else:
        guestsplit_m = []
    # 🔒 SPLIT SELF-CHECK (always-on): MEASURE both panels' face size so the guest never ships as a GIANT close-up
    # (zoom 2.0 once made the guest's face 57% of its panel vs the host's 21%). WARN + suggest a corrected zoom.
    def _panel_face_pct(masters):
        try:
            import statistics as _st
            mp = masters[0][2] if masters else None
            DF = os.path.join(os.path.dirname(REFRAME), "detect_face_dense.py")
            if not (mp and os.path.exists(mp) and os.path.exists(DF)): return None
            ck, fj = mp + ".pchk.mp4", mp + ".pchk.json"
            subprocess.run([FF, "-y", "-loglevel", "error", "-i", mp, "-t", "2", "-an",
                            "-vf", f"crop={OW}:{OH//2}:0:{120*OW//1080}", "-c:v", "libx264", "-preset", "ultrafast", ck], capture_output=True)
            import sys as _sys
            subprocess.run([_sys.executable, DF, ck, fj, "1280"], capture_output=True)
            d = json.load(open(fj)); pic = [r for r in d.get("curve", []) if r.get("conf", 0) > 0.5 and r.get("face_h")]
            for _p in (ck, fj):
                try: os.remove(_p)
                except OSError: pass
            return _st.median([r["face_h"] for r in pic]) / (OH // 2) if pic else None
        except Exception as _e:
            print(f"  [split][check] skipped ({type(_e).__name__}: {_e})", flush=True); return None
    def _panel_open_face(masters):
        """OPEN-WINDOW presence+position on REAL pixels (first ~0.5s). MUST measure PRESENCE FRACTION across the
        window, NOT the median — the StageQA RegretTest defect was the guest ABSENT for the first 0.40s then
        appearing at 0.40s; a median over the window is diluted by the present frames and masks the dead open.
        Returns (presence_fraction, median_face_cy_fraction_of_present) or None if it couldn't check."""
        try:
            import statistics as _st, sys as _sys
            mp = masters[0][2] if masters else None
            DF = os.path.join(os.path.dirname(REFRAME), "detect_face_dense.py")
            if not (mp and os.path.exists(mp) and os.path.exists(DF)): return None
            ck, fj = mp + ".ochk.mp4", mp + ".ochk.json"
            # sample 2s: presence over the FIRST 0.5s catches the headless OPEN; median cy over the whole 2s
            # catches a SUSTAINED too-low guest (the StageQA V2 defect: guest framed at 55% of panel vs 34%).
            subprocess.run([FF, "-y", "-loglevel", "error", "-i", mp, "-t", "2.0", "-an",
                            "-vf", f"crop={OW}:{OH//2}:0:{120*OW//1080}", "-c:v", "libx264", "-preset", "ultrafast", ck], capture_output=True)
            subprocess.run([_sys.executable, DF, ck, fj, "1280"], capture_output=True)
            d = json.load(open(fj)); curve = d.get("curve", [])
            for _p in (ck, fj):
                try: os.remove(_p)
                except OSError: pass
            if not curve: return None
            open_win = [r for r in curve if r.get("t", 9) <= 0.5]
            open_present = [r for r in open_win if r.get("conf", 0) > 0.5 and r.get("face_cy") is not None]
            present = [r for r in curve if r.get("conf", 0) > 0.5 and r.get("face_cy") is not None]
            pf = (len(open_present) / len(open_win)) if open_win else (len(present) / len(curve))  # OPEN presence (first 0.5s)
            cy = (_st.median([r["face_cy"] for r in present]) / (OH // 2)) if present else None     # SUSTAINED position (2s)
            return (pf, cy)
        except Exception as _e:
            print(f"  [split][open-check] skipped ({type(_e).__name__}: {_e})", flush=True); return None
    if split_m and guestsplit_m:
        _hp, _gp = _panel_face_pct(split_m), _panel_face_pct(guestsplit_m)
        if _hp and _gp:
            print(f"  [split][check] panel face%: host(speaker)={_hp*100:.0f}%  guest={_gp*100:.0f}%  (good guest ~28-38%; giant >45%)", flush=True)
            # WARN only on a true GIANT close-up (the zoom-2.0 bug was 57% = 2.7x host); a guest naturally runs a bit
            # larger than the host, so don't trip on the normal ~30-38% head-and-shoulders. Suggest a zoom targeting ~30%.
            if _gp > 0.45 or _gp > 2.2 * _hp:
                _sugg = round(float(_gsz) * (0.30 / _gp), 2)
                print(f"  [split][WARN] guest face is {_gp*100:.0f}% of its panel vs host {_hp*100:.0f}% — likely a GIANT close-up. "
                      f"Lower guest zoom {_gsz} -> ~{_sugg} via sync['guest_split']['zoom'] (targets ~30%).", flush=True)
        # 🔒 GUEST PANEL GATE (2026-06-17, BLOCKS — gate, don't warn) — two real-pixel checks on the guest panel:
        #   (1) OPEN PRESENCE — guest must be IN FRAME in the first 0.5s (the RegretTest V1 headless-open: 0% for 0.40s).
        #   (2) SUSTAINED POSITION — guest face must sit near the 34% target, not low (the RegretTest V2 defect: the
        #       guest framed at 55% of the panel — a mis-calibrated guest_split.roi → poor detection → back-filled low).
        _of = _panel_open_face(guestsplit_m)
        if _of is not None:
            _pf, _cy = _of
            _toolow = _cy is not None and _cy > 0.45   # target 0.34; 0.45 sits between good (~0.34) and the V2 defect (0.55)
            if _pf < 0.5 or _toolow:
                import sys as _sys
                _why = (f"ABSENT for {(1-_pf)*100:.0f}% of the open (out of frame at t=0)" if _pf < 0.5
                        else f"too LOW (face center at {_cy*100:.0f}% of panel vs 34% target)")
                print(f"  [split][BLOCK] guest face is {_why}. "
                      f"guest_panel_render now recovers leaning-to-mic open frames; if this still trips, the guest_split.roi "
                      f"is mis-calibrated for this cam — WIDEN it (lower the bottom of the height band, e.g. \"0.08 0.05 0.40 0.78\") "
                      f"then re-render. NOT shipping a headless open.", flush=True)
                _sys.exit(2)

    # ---------- VIDEO: one clip per segment (the angle), concat ----------
    print(f"  [video] rendering {len(segs)} segments ...", flush=True); vids = []
    for i, s in enumerate(segs):
        vs, ve = segdur[i]                                      # snapped seam boundaries -> keep A/V in sync
        d = ve - vs; out = f"{work}/v{i:03d}.mp4"
        if s["cam"] in masters:     # any reframed angle: speaker / speaker_close / guest / guest_close (tracked, Y-locked, box-centered)
            mp, r0 = lookup(masters[s["cam"]], vs, s["cam"]); ss = vs - r0
            g = grade_for(s["cam"]); vf = "setsar=1" + (("," + g) if g else "")
            run([FF, "-y", "-loglevel", "error", "-ss", f"{ss:.3f}", "-i", mp, "-t", f"{d:.3f}", "-an", "-vf", vf,
                 "-r", FPS, *encoder_args(OW, OH, FF, tier="delivery"), out])
        elif s["cam"] == "split":     # Speaker top + guest bottom: BOTH face-tracked reframe masters, each cropped to a
                                      # panel (face Y-locked to the upper-third). + one-sided drop-shadow seam.
            mtop, rt = lookup(split_m, vs, "speakersplit")
            mbot, rb = lookup(guestsplit_m, vs, "guestsplit")
            atop = ("," + SPEAKER_GRADE) if GRADE else ""; abot = ("," + GUEST_GRADE) if GRADE else ""
            # PANEL Y-OFFSET = which 1920-tall (4K) slice of the 3840-tall reframe master fills the panel.
            # 2026-06-17 (Operator, Guest): the guest sat too LOW in his panel. At guest zoom<=1 the reframe's
            # cropH clamps to full source height so eye_y is dead — the ONLY vertical lever is THIS offset.
            # Bigger offset => shows a LOWER slice of the master => subject moves UP in the panel (and reveals
            # more chest = reads less zoomed). Top (Speaker) keeps the default 240; guest is independently tunable
            # via qa_sync.guest_split.panel_y (4K px, default 240).
            _topy = 120 * OW // 1080
            pcrop_t = f"crop={OW}:{OH//2}:0:{_topy},setsar=1"
            if _gs_panel:
                # TARGET mode: guest master IS ALREADY the framed panel (OW x OH//2, face solved to
                # target_face_y by guest_panel_render). Use it as-is — the legacy _boty slice would
                # double-shift it (StageQA RegretTest). Only the legacy fixed-zoom master needs _boty.
                pcrop_b = "setsar=1"
            else:
                _boty = int(_gs.get("panel_y", _topy)) if OW >= 2160 else int(_gs.get("panel_y", 120) * OW // 2160)
                _boty = max(0, min(OH - OH // 2, _boty))   # clamp so the crop stays inside the master
                pcrop_b = f"crop={OW}:{OH//2}:0:{_boty},setsar=1"
            fc = (f"[0:v]{pcrop_t}{atop}[top];[1:v]{pcrop_b}{abot}[bot];"
                  f"[top][bot]vstack[stk];[2:v]scale={OW}:-1[sh];[stk][sh]overlay=0:{OH//2}[v]")
            run([FF, "-y", "-loglevel", "error", "-ss", f"{vs-rt:.3f}", "-i", mtop,
                 "-ss", f"{vs-rb:.3f}", "-i", mbot, "-loop", "1", "-i", SHADOW, "-t", f"{d:.3f}", "-an",
                 "-filter_complex", fc, "-map", "[v]", "-r", FPS, *encoder_args(OW, OH, FF, tier="delivery"), out])
        elif s["cam"] == "guest_wide":   # C2118 wide ROOM shot of the guest standing (the 3rd cam)
            cam, off = role2["wide"]
            vf = GUEST_WIDE_CROP + (("," + GUEST_GRADE) if GRADE else "")
            run([FF, "-y", "-loglevel", "error", "-ss", f"{vs-off:.3f}", "-i", cam, "-t", f"{d:.3f}", "-an", "-vf", vf,
                 "-r", FPS, *encoder_args(OW, OH, FF, tier="delivery"), out])
        else:    # wide cam (C2118) — stage side
            cam, off = role2[s["cam"]]
            vf = WIDE_CROP + (("," + SPEAKER_GRADE) if GRADE else "")
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

    # ---------- MUSIC BED (optional): vibe-matched track mixed UNDER the -14 voice. loudnorm to a consistent bed
    # so every clip sits the same, gentle afade in/out, amix normalize=0 to PRESERVE the voice level, + a 0.12s
    # voice fade-in to mask any leading filler/breath. ENDING IS A HARD CUT (the music's out-fade only softens the
    # tail ring, never a video fade). Per-clip DISTINCT track, Speaker lane (score-texture / instrumental trap), never
    # the Calm folder — see feedback_speaker_music_selection_logic. ----------
    FINAL_A = f"{work}/amaster.wav"
    if a.music:
        mp = a.music if os.path.isabs(a.music) else os.path.abspath(a.music)
        if not os.path.exists(mp): raise SystemExit(f"--music file not found: {mp}")
        adur = dur_of(FINAL_A)
        delay_ms = int(round(max(0.0, a.music_delay) * 1000))
        # DELAY (2026-06-16, Operator review): insert `delay_ms` of silence before the music bed in the
        # OUTPUT timeline. Use when the clip OPENS on a split-screen Q+guest intro — music kicks in
        # only when the camera cuts to Speaker's answer. adelay applies to the LOUDNORMED + faded music
        # stream BEFORE amix, so the leading silence in the bed plays cleanly under the voice.
        delay_filter = f"adelay={delay_ms}|{delay_ms}," if delay_ms > 0 else ""
        fc = (f"[0:a]afade=t=in:st=0:d=0.12[v];"
              f"[1:a]loudnorm=I={a.music_level}:TP=-3:LRA=11,afade=t=in:st=0:d=0.8,"
              f"afade=t=out:st={max(0, adur-1.0-a.music_delay):.3f}:d=1.0,{delay_filter}"
              f"atrim=0:{adur:.3f},asetpts=PTS-STARTPTS[m];"
              f"[v][m]amix=inputs=2:duration=first:normalize=0,"
              f"loudnorm=I=-14:TP=-1.5:LRA=11[mix]")   # re-normalize the voice+music mix back to -14 (bed shifts gating)
        run([FF, "-y", "-loglevel", "error", "-i", FINAL_A, "-ss", f"{a.music_ss:.3f}", "-i", mp,
             "-filter_complex", fc, "-map", "[mix]", "-ar", "48000", "-ac", "2", f"{work}/amaster_mus.wav"])
        FINAL_A = f"{work}/amaster_mus.wav"
        print(f"  [music] bed under -14 voice (loudnorm I={a.music_level}, hard out, delay={a.music_delay:.2f}s): {os.path.basename(mp)}", flush=True)

    base = f"{work}/base.mp4"
    run([FF, "-y", "-loglevel", "error", "-i", f"{work}/vsilent.mp4", "-i", FINAL_A, "-map", "0:v", "-map", "1:a",
         "-map_metadata", "-1", "-dn", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", "-shortest", base])
    print(f"  [mux] base built ({dur_of(base):.2f}s)  ({time.time()-t0:.0f}s)", flush=True)

    if a.no_captions:
        shutil.copy(base, a.out)
        print(f"\nDONE (no captions) -> {a.out}  {dur_of(a.out):.2f}s  total {time.time()-t0:.0f}s")
        if not a.keep_temp: shutil.rmtree(work, ignore_errors=True)
        return

    # ---------- CAPTIONS: transcribe -> [corrections] -> lowercase -> money -> EDL-driven director -> spice -> HW burn ----------
    print("  [captions] transcribe + spice ...", flush=True)
    run([sys.executable, TRANSCRIBE, base, "--start", "0", "--end", f"{dur_of(base):.2f}", "--out", f"{work}/w_raw.json"])
    # CORRECTIONS (2026-06-15): apply per-clip word/phrase fixes for Whisper mishearings on the
    # rendered audio. Mirrors spice_caption.py's --corrections logic (phrase pass first, then
    # single-token, case-insensitive on the bare word, punctuation preserved). Use this when the
    # source audio is what it is but Whisper consistently mishears a word/phrase in the cut.
    # Examples seen on Tier1 batch: "trading" -> "draining"; "when like" -> "when"; "founder's
    # problem" -> "the founder. I fulfill" (a phrase swap that recovers the actual spoken arc).
    if a.corrections and os.path.exists(a.corrections):
        import re as _re
        cmap = {k.lower(): v for k, v in json.loads(open(a.corrections).read()).items()}
        wj = json.load(open(f"{work}/w_raw.json"))
        words = wj.get("words", [])
        def _bare(w): return _re.sub(r"^\W+|\W+$", "", w.get("word", ""))
        n_fixed = 0
        # PHRASE pass first: multi-token keys (e.g. "when like" -> "when")
        phrase_keys = [k for k in cmap if " " in k]
        for k in phrase_keys:
            tokens = k.split()
            n = len(tokens)
            i = 0
            while i <= len(words) - n:
                if all(_bare(words[i + j]).lower() == tokens[j] for j in range(n)):
                    repl = cmap[k].split()
                    # collapse the n words into len(repl) words, preserving timing
                    t0_ = words[i]["start"]; t1_ = words[i + n - 1]["end"]
                    new_words = []
                    if repl:
                        step = (t1_ - t0_) / max(1, len(repl))
                        for j, rw in enumerate(repl):
                            new_words.append({"word": rw + (words[i + n - 1].get("word","")[-1] if j == len(repl)-1 and words[i + n - 1].get("word","")[-1] in ".,!?;:" else ""),
                                              "start": t0_ + j * step, "end": t0_ + (j + 1) * step})
                    words[i:i + n] = new_words
                    n_fixed += 1
                    i += len(new_words)
                else:
                    i += 1
        # SINGLE-TOKEN pass
        for i, w in enumerate(words):
            bare = _bare(w).lower()
            if bare in cmap and " " not in bare:
                tail = w.get("word", "")
                m = _re.search(r"[.,?!;:]+$", tail)
                punct = m.group(0) if m else ""
                w["word"] = cmap[bare] + punct
                n_fixed += 1
        wj["words"] = words
        json.dump(wj, open(f"{work}/w_raw.json", "w"))
        print(f"  [captions] corrections applied: {n_fixed} word/phrase fix(es) from {a.corrections}", flush=True)
    run([sys.executable, NORMALIZE, f"{work}/w_raw.json", f"{work}/w_norm.json"])     # lowercase (guide rule)
    run([sys.executable, SPICE_NORM, f"{work}/w_norm.json", f"{work}/w_spice.json"])  # $ / % / unit tokens
    # Auto-trim leading/trailing FILLER captions (don't show a stray "so/and/yeah" at the very start/end — the audio
    # still plays; this is a caption-only trim). Conservative discourse-marker set; never touches the middle or "I".
    _spice = json.load(open(f"{work}/w_spice.json")); words = _spice["words"]
    FILLER = {"yeah","yep","yup","mm","mhm","hmm","uh","um","uhh","so","and","but","well","okay","ok","right","alright","like","now"}
    def _isfill(w): return "".join(c for c in str(w.get("word","")).lower() if c.isalpha()) in FILLER
    _lo, _hi = 0, len(words)
    while _lo < _hi and _isfill(words[_lo]): _lo += 1
    while _hi > _lo and _isfill(words[_hi-1]): _hi -= 1
    _orig_n = len(words)
    if (_lo, _hi) != (0, _orig_n):
        words = words[_lo:_hi]; _spice["words"] = words
        json.dump(_spice, open(f"{work}/w_spice.json", "w"))          # generate_spice reads this trimmed file
        print(f"  [captions] filler-trim: dropped {_lo} leading + {_orig_n-_hi} trailing filler caption(s)", flush=True)
    # clip-time -> speaker, from the ACTUAL (snapped) segment durations
    cr, t = [], 0.0
    for i, s in enumerate(segs):
        d = segdur[i][1] - segdur[i][0]; cr.append((round(t, 3), round(t + d, 3), s["speaker"])); t += d
    json.dump(cr, open(os.path.splitext(a.out)[0] + "_clipmap.json", "w"))   # for qa_audit per-speaker checks
    def speaker_at(ct):
        for x0, x1, sp in cr:
            if x0 <= ct < x1: return sp
        return cr[-1][2]
    # PER-WORD SPEAKER (2026-06-10, Operator): the EDL 'speaker' is per CAMERA SHOT, so a short
    # turn-reply ("yes", "cool") at a boundary was colored for the wrong person. resolve_speakers
    # decides each word by MIC ENERGY (separate per-speaker mics = ground truth) + CONVERSATIONAL
    # TURN-TAKING (an ambiguous reply token belongs to the responder = the other speaker). Shared
    # with qa_build so the rule is identical everywhere; falls back to the EDL shot-speaker.
    if sync.get("speaker_color_from_edl"):
        # Stage Q&A (2026-06-17, StageQA): the "guest" mic is a ROOM/PA-stand mic that hears Speaker via the
        # PA, so mic-energy diarization mis-tags Speaker's held singles as guest (yellow). When the EDL
        # shot-speakers are hand-verified, trust them for caption COLOR instead of mic energy.
        edl_guest = [i for i, w in enumerate(words) if speaker_at((w["start"] + w["end"]) / 2) == "guest"]
        print(f"  [captions] speaker color from EDL ({len(edl_guest)} guest words)", flush=True)
    else:
        edl_guest, _ = resolve_speakers(words, segdur, segs, speaker_at, spk_mic, cam_dir, FF,
                                        debug=bool(os.environ.get("VIBE_DIAR_DEBUG")))
    director = {}
    _dr = subprocess.run([sys.executable, f"{CAPS}/scripts/caption_director.py", f"{work}/w_spice.json",
                          "--out", f"{work}/director_llm.json", "--context", "q&a workshop"],
                         capture_output=True, text=True)
    if _dr.returncode == 0 and os.path.exists(f"{work}/director_llm.json"):
        llm = json.load(open(f"{work}/director_llm.json")).get("words", {})
        for k, v in llm.items():
            v = dict(v); v.pop("c", None)   # EDL owns COLOR; keep the director's weight/size/italic (size axis ON 2026-06-11)
            director[k] = v
        print(f"  [captions] director: {len(director)} weighted words (gold-bank few-shot)", flush=True)
    else:
        print(f"  [captions] director LLM unavailable; weight-flat fallback ({_dr.stderr.strip()[-80:]})", flush=True)
    for i in edl_guest:                                       # EDL color truth: guest = yellow + italic
        d = director.setdefault(str(i), {}); d["c"] = "guest"; d["i"] = True
    json.dump({"words": director, "voice_spans": []}, open(f"{work}/director.json", "w"))
    # PER-ANGLE caption Y: analyze the FINAL reframed video and feed the layout track to
    # generate_spice so the caption HEIGHT moves WIDE 45% / TIGHT 50% per shot — matching
    # The reference editor's measured placement (training/height_study, 599 shots: wide stage ~45%, medium/
    # closeup/split ~50%). Without this the captions sat at one static Y for every angle.
    # Falls back to the preset's static 50% if the analyzer is unavailable.
    cc_layout = None
    if os.path.exists(LAYOUT_ANALYZE):
        _la = subprocess.run([sys.executable, LAYOUT_ANALYZE, base, f"{work}/cc_layout.json",
                              "--sample-every", "2", "--detect-width", "800"],
                             capture_output=True, text=True)
        if _la.returncode == 0 and os.path.exists(f"{work}/cc_layout.json"):
            cc_layout = f"{work}/cc_layout.json"
            print(f"  [captions] per-angle Y track built (wide 45% / tight 50%)", flush=True)
        else:
            print(f"  [captions] layout_analyze unavailable; static Y ({_la.stderr.strip()[-80:]})", flush=True)
    # LOCKED SHADOW: let caption-clips do the BURN so /edit gets the real two-layer gblur
    # Premiere drop shadow (cc_shadow/cc_shadow2/cc_text composited via gblur), NOT the weak
    # inline single-layer ASS shadow (\bord\blur in cc.ass) the old subtitles= burn used.
    # caption-clips is the SSOT for the shadow — delegate the burn to generate_spice --burn.
    cc_burned = f"{work}/cc_burned.mp4"
    gen_cmd = [sys.executable, GEN_SPICE, f"{work}/w_spice.json", "--preset", a.preset,
               "--style", f"{work}/director.json", "--out", f"{work}/cc.ass",
               "--burn", base, "--burn-out", cc_burned]
    if cc_layout:
        gen_cmd += ["--layout", cc_layout]
    run(gen_cmd)
    _lint = f"{CAPS}/scripts/caption_lint.py"   # self-audit gate (advisory): enforce locked rules pre-burn
    if os.path.exists(_lint):
        subprocess.run([sys.executable, _lint, f"{work}/cc.ass"])
    if not os.path.exists(cc_burned):
        sys.exit(f"caption burn failed — {cc_burned} missing")
    # Finishing pass on the gblur-shadowed burn (sync-safe — captions are already baked in, so a
    # start pad shifts text+picture together): (1) ~3-frame video lead before audio via tpad clone
    # + matching audio adelay (prevents the swipe audio-pop); (2) true-peak limiter to ~-6dB so the
    # final isn't hot/clipping. Re-encode audio to apply the delay+limiter.
    run([FF, "-y", "-loglevel", "error", "-i", cc_burned,
         "-vf", "setsar=1,tpad=start_mode=clone:start_duration=0.1",
         "-af", "adelay=100|100,alimiter=limit=0.5:level=disabled",
         "-r", FPS, *encoder_args(OW, OH, FF, tier="delivery"), "-map_metadata", "-1", "-dn",
         "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", a.out])
    g = len(edl_guest); print(f"  [captions] {len(words)} words, {g} guest(yellow-italic) / {len(words)-g} speaker(white)", flush=True)

    print(f"\nDONE -> {a.out}  {dur_of(a.out):.2f}s  total {time.time()-t0:.0f}s")
    if not a.keep_temp: shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
