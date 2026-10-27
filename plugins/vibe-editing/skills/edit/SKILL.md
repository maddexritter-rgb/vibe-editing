---
name: edit
description: >
  Orchestrate raw footage into short-form clips: ingest, source inspection, moment selection,
  cutting, reframing, captions, music, rendering and checks. Use for make clips, make shorts,
  cut clips, mine clips, podcast clips, Q&A clips, hotline clips or multicamera edits.
  Connect the current editing providers through onboarding before first production use.
---

# edit — THE master editing skill

> **📦 PLUGIN PATHS — read once.** This skill ships inside the **`vibe-editing`** plugin.
> Every command below references **`${CLAUDE_PLUGIN_ROOT}`** = this plugin's install directory
> (the folder that contains `skills/`, `lib/_shared/`, and `vault/`). Claude Code sets that
> variable for plugin contexts; if a shell you run doesn't have it, resolve it once at the start
> of the job — `export CLAUDE_PLUGIN_ROOT="$(cd "$(dirname "$(command -v ... )")" ...)"` or simply
> set it to the plugin dir you installed. **You usually don't need to:** every bundled Python
> script self-locates its own `lib/_shared`, sibling skills, and `vault/` by walking up to the
> `.claude-plugin/` marker (it also honors `VIBE_PIPELINE_ROOT`), so only the *entry* script path
> in each command has to be right. **User-supplied assets** (not shipped with the plugin): the
> licensed **music library** → put it at `${CLAUDE_PLUGIN_ROOT}/vault/content-skill-system/(1) Tik Tok/`
> or point `VIBE_MUSIC` at it; the ~11GB **overlay/transition asset pack** → `${CLAUDE_PLUGIN_ROOT}/assets/`
> or point `VIBE_ASSETS` at it. The core cut→reframe→caption→render→audit path needs neither. See the
> plugin's `INSTALL.md`.
>
> **First run:** follow [onboarding](../../docs/ONBOARDING.md) before production. The current
> `/edit` uses AssemblyAI primary ingest and Gemini listener notes. **The active model reads the
> bundled editorial documents and makes the cut/style decisions itself. No Anthropic/OpenAI key,
> separate Claude CLI, or per-editor brain token is required.** See [connections](../../docs/CONNECTIONS.md).
> For preconfigured live team access, run `editorial_brain.py --format <format> --out <project>/10_WORK/brain/context.v1.json`
> from the plugin root and read the full document receipt. If access is not provisioned, identify
> the bundled references as bundled guidance rather than claiming a live read.
> This ownership rule overrides historical scripted selector/director recipes below. Those older
> external-model helpers are not the default execution path.

> **🛑 POST-MORTEM FROM Tier1 Q&A BATCH 2026-06-14 → 2026-06-16 — READ FIRST IF EDITING Q&A**
>
> 8 rounds of revisions on an 18-clip batch before Operator was satisfied. ALL the defects were preventable with stricter pre-/post-build audit. Root causes + what to do every Q&A run, going forward:
>
> ### Why the mistakes happened
> 1. **I treated playbook non-negotiables as advisory** — silently fell back to "simplest valid floor" (single-cam Speaker) instead of doing split-screen. The playbook EXPLICITLY listed split-screen as non-negotiable #2. **FIX:** `non_negotiables_check.py` runs as a HARD GATE. Defensive pre-justification in the delivery README ("happy to do X as a V2 pass") is the smell — if I'm writing it before the user complains, I'm cutting a corner.
> 2. **I configured visual params (zoom, eye) once GLOBALLY and shipped without per-clip visual verification.** Guests differ — Guest (short) needs zoom=1.15/eye=0.18; Guest (tall) hits the reframer's source-bound limit at the same settings; Guest (medium) worked at 0.95/0.10. There is NO single zoom that satisfies all guests. **FIX:** Step 9 now requires per-clip OPEN-FRAME extraction + visual face-position measurement BEFORE staging.
> 3. **I shipped music tracks with slow build-up intros without profiling.** øfdream 27s, øneheart 17s, Gods creation 25s, Labrinth 4s — all bled boring intro under the hook. **FIX:** STEP 7 of qa_playbook.md mandates RMS profile of every picked track; if beat-drop > 1s, `--music-ss <N>` is required. Audit gate G5 automates this.
> 4. **I cut segments on Whisper's word.end labels without padding for soft consonants.** "road." (-d) and "one." (-n) got mid-syllable clipped because Whisper's labels are 0.1–0.25s early on soft tails. **FIX:** G1 boundary-tail audit catches every flagged segment-end pre-build.
> 5. **I cut cameras mid-completion.** Cut to Speaker while Guest was still saying "yeah trying to figure out…" — the source-time jump reads as "muted guest mic." **FIX:** G2 guest-completion audit catches same-conversation cam-cuts that skip prev-speaker words.
> 6. **I ended clips on mid-thought, not the natural button.** Guest closed on "biggest, baddest one." instead of the punchier "So give the sickest one away." 1s later. **FIX:** G3 payoff-extension audit reads the 3s past EDL's last word for an imperative/connector-led button.
> 7. **I optimized one clip at a time instead of running a batch-wide audit before each rebuild.** Every revision round revealed defects I should have caught proactively. **FIX:** `qa_prebuild_audit.py` runs all 6 gates in <5s on the EDL before any 4K encode burns 5–8 min. Cheaper to fail at audit than at human review.
>
> ### The Q&A discipline going forward (autonomous-safe)
> Run this loop per clip, in this order:
> 0. **Design the CUT with the CONTRAST HOOK + HOOK→PAYOFF→TENSION method (`references/clipper_ai_prompt.md`) — the DEFAULT, not a remembered option.** WHAT-they-do→revenue hook; end on the portable PRINCIPLE, never a bare tactic/wind-down. Spine = [`references/QA_MASTER_SOP.md`](references/QA_MASTER_SOP.md).
> 1. Design EDL + corrections.json + pick music
> 2. **`qa_editorial_score.py` MUST EXIT 0** on the clip's spoken transcript (HARD GATE, not advisory — blocks plain/revenue-first hooks, tactic/wind-down endings, failed portability, sub-100-word skeletons). Compounding standard: gate, don't warn. [[feedback_compounding_standard_default_plus_gate_2026-06-12]]
> 3. **`qa_prebuild_audit.py` MUST EXIT 0** before any encode (G1–G6)
> 4. Build via qa_assembly
> 5. **Extract open frame at t=1.0s. If split-screen, measure guest face position in lower panel.** If face center > 35% of panel height → rebuild with lower eye / higher zoom OR document the source-bound limit (tall guest in centered CCAM)
> 6. Head-trim if onset > 0.10s, reqc, stage
> 7. SF-sf-audit + scorecard-audit (post-build)
> 8. ONLY then declare clip ready
>
> A clip that skips any of these steps is a clip that ships defective. The Tier1 batch took 8 rounds because I skipped steps. Don't skip steps.

> **LOCKED 2026-06-10.** One skill. One spine. Speaker count + camera count = the only branch
> points. Each downstream capability (reframe, caption, audit) lives in its OWN skill — this
> doc orchestrates, it does not duplicate.
>
> **🆕 LOCKED 2026-06-11 — DELIVERY GOES THROUGH `render`.**
> Steps 8–11 (reframe, caption, music, hard-end) and step 13 (deliver) are now a single
> step: **call the `render` skill with a `manifest.json`.** The render engine is
> manifest-driven and stage-cached — revisions on a delivered clip only re-run the changed
> stage, NOT the whole pipeline. Every new project MUST scaffold a `manifest.json` + source
> files (`cuts.json`, `captions.ass`, `music.json`) at step 0; the cut/clean/QC loop iterates
> on those source files; then `render` does the build.
>
> A caption typo revision = edit `captions.ass` + `engine.py <project> --bump` (~30s for a
> 50s listicle). A re-cut = edit `cuts.json` + same command. Never re-render from scratch
> just to change one layer.

---

## 🔒 SPEAKER SINGLE-CAM DESK/TALKING-HEAD CLIP — LOCKED RECIPE (2026-06-11)

> Hard-won on the StayInAGreatMood batch. For a 1-speaker / 1-camera Speaker desk or stage
> talking-head, these are the DEFAULTS — set them in the manifest and the clip comes out right
> the FIRST time. Each gate below maps to a real mistake made this session; do NOT repeat them.

> ### 🛑 MANDATORY OPENER GATE — RUN ON **EVERY** CLIP, EVERY TIME (not just flagged ones)
> The #1 process failure this session: the gates existed but were run REACTIVELY — only on clips
> Operator flagged — so a dead-air open (TheSecondArrow: 0.66s of silence + an "and so" before the
> hook) and a rambly aside ("I've been looking into Buddhism lately, weirdly enough") shipped.
> Before showing the user ANY clip, run BOTH checks on ALL of them — it is your job to catch
> these, NOT the user's. These are RULES, not taste:
> 1. **Onset (measure, don't eyeball):** transcribe/RMS the first ~1.5s. First audible speech must
>    be within ~50ms of frame 1, AND the first word must be the intended hook word. ASR labels lie
>    by seconds — snap the in-point to the measured acoustic onset, not `word.start`. If there's a
>    lead, trim it (`leadfix.head_trim`) or hard-set `cuts.json`, then re-measure.
> 2. **Editorial opener (cold-viewer):** the clip must open on the HOOK. CUT any preface
>    ("with all that being said"), filler conjunction ("and so", "so"), or personal aside
>    ("…weirdly enough") that sits BEFORE the hook. If you ever catch yourself writing "aside is
>    acceptable / adds personality," that's a skipped check — apply the rule and cut it.
> Do this per-clip and report the opener of each ("opens on: '<first words>', onset Xms") before delivery.

**Manifest defaults (pipeline `single`):**
```json
"reframe":  { "preset": "talking-head", "zoom": 1.4, "eye_y": 0.30, "res": "4k" }
"captions": { "preset": "<abs path to a y58 spice preset>", "no_layout": true }
"mix":      { "music": "<vibe_music.py pick>", "voice_lufs": -16, "music_lufs": -30, "limiter": 0.45 }
"leadfix":  { "head_trim": 0.0 }   // bump to ~0.08 only if the opener has a small lead beat
```

**REFRAME** → `horizontal-to-vertical` (qa_reframe_v2), always from the **4K master**, output `--res 4k`.
- **zoom 1.4** for desk talking-head. `source-intel`'s recommended zoom OVER-shoots on wide shots
  (it suggested 2.2 → "way too zoomed", head cut off). 1.4 ≈ the canon crop. Verify face has headroom.
- **PER-CUT single-pass**: the render `reframe` stage passes `--cut-frames` (from `cut.meta.segments`)
  so the tracker resets at each content seam — each cut framed independently, ONE encode. Do NOT
  per-segment-then-concat (wobble at every seam) and do NOT whole-clip-continuous (pans across seams).
- **🔒 FACE TRACKING IS THE DEFAULT — EVERY CLIP, ALWAYS. (house standard, non-negotiable.)** The X
  crop follows the subject's smoothed face-box so the camera stays on the speaker — that's what makes a
  reframe look alive, and Operator expects it on every clip. Do NOT disable it. **NEVER add `--lock-x` /
  `reframe.lock_x:true` by default** — that pins the crop static and KILLS the tracking (the bug Operator
  caught 2026-06-12: a podcast close-up shipped as a dead static crop because lock_x was set "for
  talking-heads"). When in doubt, TRACK. (Y stays locked to the eyeline via the preset — separate axis,
  always fine.)
- **`--lock-x` is a RARE EXCEPTION, NOT a podcast/talking-head default.** The ONE narrow case it exists
  for (Speaker PeaceOrPower, 2026-06-12): a near-motionless seated speaker whose micro-shifts read as a slow
  pan. Reach for it ONLY when a SPECIFIC *delivered* clip visibly shows an unwanted slow pan AND Operator
  asks to lock that clip down. Default stays tracking.

**CAPTIONS** → `caption-clips` spice via the render `captions` stage (it re-transcribes the cut
audio and runs `spice_caption.py` — it IGNORES any manifest `.ass`, so caption Y is controlled by the
PRESET, not a hand-built file).
- Style = **spice** (the the reference editor look), NEVER `pro`/`pro_locked` (legacy flat) and NEVER the
  deterministic fallback. The active agent authors per-word weight/size/italic/voice styling
  from the normalized words; pass that file as `captions.director`. No model API or CLI is invoked.
- **Height = y≈58 (renders ~59%, top of the tank-top V / lav-mic line)** via `captions.preset` +
  **`no_layout: true`**. WHY no_layout: `spice_caption`'s per-angle layout analyzer overrides the
  preset Y to ~50% on single-angle footage, which Operator calls "way too high" — and it silently
  ignores every preset/.ass edit (the cause of the long "still too high" loop). `--no-layout` honors
  the static preset Y. **UPDATE 2026-06-12 (Speaker PeaceOrPower):** podcast/multicam ALSO wants captions pinned to the
  **chest/shirt line ~60%** (`y_percent_from_top:60` + `--no-layout`), NOT the analyzer's ~50% — Operator
  showed reference clips at ~61% across BOTH camera angles (FIXED, not per-angle). Default to `--no-layout`
  at y58 (desk) / y60 (podcast); reserve the live per-angle analyzer only where per-shot height is truly wanted.

**MUSIC** → **READ `content-skill-system/(1) Tik Tok/MUSIC_RULES.md` FIRST, then use THE CALIBRATED
MATCHER: `python3 ${CLAUDE_PLUGIN_ROOT}/lib/_shared/pick_music.py --folder "(1) Calm" --used "<batch picks>"`**
— it ranks tracks by similarity to the `_APPROVED.txt` centroid (the user's ear-proven lane),
excluding `MUSIC_BLACKLIST.txt` + already-used. NEVER hand-pick by title, and do NOT use
`vibe_music.py`'s `pick_track` as the chooser — it picks RANDOM from a vibe folder, ignoring the
approved lane (2026-06-12: 5 of its picks rejected in one batch — 4Batz, SICKICK, Bon Iver,
Mild High Club, Aria Math; pattern: recognizable-song instrumentals are far from the approved
centroid). `vibe_music.py` is fine for the Groq vibe CLASSIFY step only (lane → folder), then the
calibrated matcher picks within the folder. Assign matcher ranks to clips by lane (rule 1), distinct
track per clip (rule 3), and run the SPOKEN-INTRO check (rule 6: transcribe first ~15s; lone
"Thanks for watching!"-style one-liners = Whisper hallucination on instrumentals = clean; real
clustered sentences = contaminated → next rank). When the user approves a track, ADD it to
`_APPROVED.txt` (rule 7) so the lane sharpens. The ear is the final judge — render, then offer swaps.

**CUT BOUNDARIES** → `make_cut`/word-gap, but **ASR WORD TIMESTAMPS LIE — by SECONDS, not just ms.**
This session Whisper labeled "Sometimes" 2.2s before it was spoken (at the start of a pause), so the
clip opened on dead air + a stray "said". GATES:
- **Snap the clip IN-point to the ACOUSTIC onset (RMS / silencedetect), NOT the ASR word.start.**
  Verify the first 0.1s of the delivered clip is speech (the head-trim gate, edit step 7d). If the
  Whisper label and the measured onset disagree, trust the measurement and hard-set `cuts.json`.
- **Drop opener PREAMBLE bloat** ("with all that being said," "long story short," …) — `make_cut`
  strips a known list; open on the first real hook word.
- **Cut absorbed pauses INSIDE a single ASR token** (word-gap can't see them — split the span
  explicitly or it ships a 2s dead beat mid-sentence).
- **End on the real PAYOFF, not early** — extend the out-point to Speaker's actual last point, then
  hard-end (no fade).
- **CLEAN SPLICES — snap every cut to inter-word silence** (2026-06-12, Speaker PeaceOrPower): the END label
  often sits AT the next word's start, so cutting on it lands mid-word → chopped words + clicks/pops (the
  #1 audio complaint this batch). End on the TRUE acoustic word-end, snap the cut to the quietest ~10ms
  window in the silence, ≥12ms fade. **`_shared/faded_trim_cut.py`** does exactly this AND probes/preserves
  source fps — use it on 23.976 sources where `precision_cut.py` (forces 30fps) corrupts timing. Verify
  per-cut pre/post RMS ≤ ~−28dB. Full SOP: `CLIP_CUTTING_PLAYBOOK.md`.

**VERIFY BY MEASUREMENT, NEVER BY EYE.** I misjudged caption Y as 52% when it was 66% repeatedly.
Caption position = burn-on-black or diff grade-vs-captions frames; audio onset = RMS per 0.1s;
clipping = waveform, not a re-transcript. Prove it on the REAL delivered file before "done".

**INFRA GATES:**
- **Disk**: 4K stage caches + `_segments` scratch fill the data volume fast (hit 97% / 0 bytes mid-render
  this session → corrupt output). `df -h /System/Volumes/Data` before a big batch; clear
  `v_new/*/10_WORK/stages/*/​*_segments` (regenerable) and `/tmp/*.mp4|wav|png` when low.
- **Corrupt-cache trap** (FIXED 2026-06-11): a render that dies on out-of-space leaves a partial
  `.mp4` that EXISTS — the engine used to serve it as a valid cache HIT. The engine now probes each
  cached output (`_valid_mp4`: size + ffprobe duration) and re-renders if corrupt. If you still see a
  stage's output fail to open downstream, delete that stage's cache file and re-render.

---

## 🔒 GIT DISCIPLINE — the team workflow: know where every change lives, end every session committed (2026-06-17)

> **Two layers — never conflate them:**
> - **Skill layer** = `${CLAUDE_PLUGIN_ROOT}/skills/` — the live "brain" Claude actually runs. Skills load ONLY from here. NEVER relocate it; it is not, and cannot become, the GitHub folder.
> - **Repo layer** = the GitHub copies you commit and share with the team. Project repos live in the user's `~/Documents/GitHub/` folder. (The `edit` skill is special: it lives in-place at `${CLAUDE_PLUGIN_ROOT}/skills/edit`, which is itself a git repo; the full pipeline is mirrored to a bundle repo via its `sync.sh`.)
>
> **The rule, every work session:**
> 1. **Test the change locally as a skill FIRST.** Make the skill edit → apply it to a real video → confirm it works. NEVER commit broken or untested work.
> 2. **When it works, commit it to GitHub** — at every natural stopping point and at session end. A change that exists only in the local skill (the "brain") and not in a repo is at risk and invisible to the team.
> 3. **Always be able to answer "where is this change recorded?"** — in the repo, or only as a functional skill in `${CLAUDE_PLUGIN_ROOT}/skills`? Reconcile the two before stopping (diff the live skill against its repo — *is local the same as remote?*).
> 4. **Shared, not siloed.** Skills live in GitHub so anyone can branch → improve → merge to main and everyone gets the upgrade. Innovation must not be bottlenecked by one person.
> 5. **After editing any skill, TWO repos may need updating:** the affected in-place skill repo (commit + push) **and** the pipeline bundle (run its `sync.sh`, which re-copies every skill and pushes). Don't leave one stale.
>
> Practical default: at the end of any session where a skill changed, commit + push the affected repo(s). If unsure, ASK "should I commit this to GitHub?" — once it's tested and working, the answer is yes.

---

## 🛑 NO FALSE ENDINGS — universal hard gate, EVERY clip, EVERY domain (2026-06-17, CMO note)

> The CMO's #1 note on the first ad batch: *"often the AI cuts off the speaker and goes to the end
> bumper."* A clip that ends MID-SENTENCE — speaker still talking — then jumps to the end card / hard-end
> reads as broken. **This is now a HARD GATE on every `/edit` delivery, for ALL content domains — Q&A,
> workshops, ads, listicle, podcast, monologue, anything. A clip with a cut-off sentence does NOT ship.**
>
> **The rule (one canonical home: `_shared/ending_check.py`):** the clip's last kept word must be
> **sentence-terminal in the source**, OR the next source word must begin a **new turn** (interviewer
> question / new sentence). If the SAME speaker keeps going on the same clause → FALSE ENDING → extend to
> the true sentence end (or pick a self-contained closing line). Also a false ending if the clip ends ON a
> connector/function word (and/but/so/which/to/the…).
>
> **Enforced in TWO places (defense in depth):**
> - **PRE-cut — `window_validator.py` Rule 4 PAYOFF_TRUNCATED (Step 5a).** Now also catches a CONTENT-word
>   ending where the source continues (not just function-word endings). ERROR = BLOCK before any render.
> - **POST-delivery — `reqc.py` FALSE-ENDING gate (Step 8b).** Runs on the actual delivered file, every
>   render incl. `--bump`. Pass **`--project <clip_dir>`** so it maps the clip's last word back to the
>   source and sees the continuation (without it, only a connector ending is catchable from the tail).
>
> Both call the same `ending_check`. WHY the testimonial batch shipped two cut-offs (TenOutOfTen "…to the
> next **level**", GivingItAway "…all **away,**"): `build_ad.py` skipped the gate. Lesson — **any
> clip-builder, in ANY domain, MUST run the ending gate.** Don't assume hand-picked boundaries are
> complete; verify against the source.

## 🔒🔒 THE THREE LAWS OF REVISION — non-negotiable, this is what makes the workflow safe to autonomate (2026-06-12)

> The goal is a FULLY AUTONOMOUS editing workflow. It only works if the safety gates run themselves
> and BLOCK on failure — they cannot depend on the operator remembering to re-check. Every law below
> is a real, painful regression from the StayInAGreatMood batch: round 1 shipped clean, then a long
> tail of revisions made it WORSE and re-introduced errors round 1 never had. Encode these or repeat them.

> **LAW 1 — EVERY render re-runs the FULL automated gate. A spot-check is NEVER enough.**
> The #1 meta-failure: after round 1 passed, each revision (music swap, recut, the Y fix, a caption
> tweak) was followed by checking ONLY the one thing changed. Regressions slipped through unaudited and
> shipped. RULE: after ANY `engine.py` run — first build OR `--bump` revision — run
> `python3 ${CLAUDE_PLUGIN_ROOT}/skills/edit/scripts/reqc.py <delivered.mp4>` (or `--batch <20_DELIVER/vN>`) on the
> ACTUAL delivered file, and it must exit 0 before the clip is collected/shown. No exceptions, no "I
> only changed the music so the cut is fine" — re-run the whole gate. reqc.py is the automated form of
> the PER-CLIP QC LEDGER below.

> **LAW 2 — Boundary edits are PER-CLIP, with full word context. NEVER mechanically batch-move cuts.**
> The single worst move of the session: a script that auto-snapped 13 cut OUT-points to the nearest RMS
> "valley" across the batch. A valley-snap is blind — it doesn't know if it's slicing a word in half or
> leaving half a double-take, and it pulls the cut EARLIER, which CLIPS MORE of the word. RULES:
> (a) change one boundary at a time, looking at the word-level transcript + RMS around THAT seam.
> (b) a clipped-word fix EXTENDS the OUT past the word's true acoustic end (ceiling = next word's onset),
>     it does NOT retract to a valley. (c) never run a loop that moves many boundaries by a rule.

> **LAW 3 — VERIFY THE FIX ACTUALLY ACHIEVED ITS GOAL on the delivered file. "I edited the spec" ≠ fixed.**
> SadnessVsAnxiety shipped a double-take TWICE because an early "fix" only removed the PAUSE between the
> two takes, not a take — and the result was never re-checked. RULE: after a fix, prove the specific
> defect is gone on the rendered output (transcribe the head for an opener fix; n-gram/listen for a
> double-take; waveform/ear for a clipped word) — not on the spec, not by assuming.

> **LAW 4 — NEVER trim a within-take SILENCE pause on single-cam gesturing footage. It jump-cuts.**
> Removing an internal pause hard-cuts from the subject's position before the pause to his position
> after — if he MOVED during it (gesturing, drawing, leaning), his body teleports. 61 such jump cuts
> shipped in one batch from an auto dead-air splitter (build_cuts `split_silences`). The discriminator:
> a same-take seam is a jump ONLY when the removed gap is PURE SILENCE (no words). A gap WITH words is
> an intentional content cut (removed double-take) and its jump is the accepted cost. RULE: keep spans
> CONTINUOUS through internal pauses (the pause is usually him drawing = content); only trim a pure-
> silence pause after confirming LOW motion across it (full-frame Δ ≤ ~14). reqc.py's JUMP-CUT gate
> (`--project`) blocks pure-silence seams with cross-seam Δ>14. (This is the reconciliation of the old
> "cut tighter" pressure: tighten by motion, not blindly.)

> **LAW 5 — A reframe/zoom/format change REBUILDS via the engine; NEVER re-mux a foreign audio track onto a re-cut video.**
> Studio zoom-out (2026-06-16): I re-cut each answer's VIDEO from source but re-muxed the ORIGINAL saved
> audio (`-map <foreign>:a`) to keep the music. The re-cut video and the saved audio had DIFFERENT cut points
> → audio/video DESYNC ("the audio continues but his cut changes") + caption MISALIGNMENT (captions off a
> third timeline) on the WHOLE batch. Brand review called every clip terrible. RULE: to change framing/zoom,
> re-run the engine manifest with the new reframe — the cut stage cuts video AND audio from the SAME in/out
> (synced) and captions regen from THAT audio (aligned). Put music in the MIX stage so the clip's own audio
> is lav+music. Concat Q+A with `concat=n=N:v=1:a=1` (each clip's OWN audio) — NEVER a foreign `-map x:a`.
> (The off-camera QUESTION audio is the ONLY safe swap: isolation preserves timing, Speaker isn't lip-syncing it.)
> Also: a "contiguous" answer usually still has small dead-air trims — let the engine cut it, don't assume one
> span. See [[feedback_revision_engine_one_cut_2026-06-16]].

> **ASR BLIND SPOTS (relearned every session — stop relearning them):**
> - **Clipped words are INVISIBLE to a re-transcript** (ASR reads a chopped word as whole). Confirm word
>   integrity by waveform/spectrogram or EAR. An "ASR-timestamp clipped-word scan" is NOISE — it flags
>   every sentence-final word (proved 2026-06-12); do not build or trust one.
> - **Whisper word timestamps lie by up to ~2s at zero-gap boundaries.** Snap in/out to MEASURED acoustic
>   onsets/valleys (RMS per 10ms), never to `word.start`/`word.end`.
> - **Double-takes hide inside stretched tokens** (a false-start absorbed into one long ASR word). The
>   n-gram repeat scan in reqc.py + the EAR catch these; reading the prose transcript does not.

---

## 🔒 PER-CLIP PRE-DELIVERY QC LEDGER — RUN ON **EVERY** CLIP, EVERY TIME (2026-06-11)

> The human-readable form of `reqc.py` (Law 1). **Run this whole checklist on EVERY clip before showing
> the user — proactively, on every render, not just clips they flag.** Verify by MEASUREMENT
> (RMS/ffprobe/transcribe/diff/EAR), never by eye, never by re-transcript alone.

**OPENER (every clip):**
- [ ] First audible speech within ~50ms of frame 1 — measure RMS, don't trust the ASR `word.start`
      (it lied by 0.66s on TheSecondArrow, 2.2s on AFewBadDays). Snap in-point to the acoustic onset;
      trim residual lead with `leadfix.head_trim` or hard-set `cuts.json`.
- [ ] First word is the HOOK — not a preamble ("with all that being said"), filler conjunction
      ("and so"), or an aside ("…weirdly enough, I've been into Buddhism lately"). Cut it. If you
      think "the aside adds personality," that's a skipped check — cut it.

**WORDS / CUT (every segment boundary):**
- [ ] No clipped boundary word — soft endings (-ful, -al, -s, -t, -th, -ing, -tion) get true-ended
      LATE; ASR `word.end` is ~0.1–0.25s early. "painful" and "optional" both shipped clipped. Pad the
      OUT-point past the soft tail (verify the next word's onset is the ceiling).
- [ ] No absorbed pause INSIDE one ASR token (a 2s dead beat mid-sentence). Word-gap can't see it —
      split the span explicitly around it.
- [ ] No double-take / restart — Speaker often says a phrase, restarts, says it again
      ("Anxiety comes from many options. Anxiety comes from many options, but…"). Keep the COMPLETE
      take, cut the redundant one.
- [ ] No mid-clip filler — "you know" / "I mean" / "um/uh" (manual eye + transcript scan; word-gap
      doesn't remove them). 
- [ ] Ends on the REAL payoff, not early (DontCatchIt ended before Speaker's point). Extend the out to
      the actual last line; hard-end (no fade).
- [ ] 🛑 NO FALSE ENDING / cut-off sentence — last word is sentence-terminal in the SOURCE, OR the
      interviewer takes over next. If the SAME speaker keeps going, EXTEND to the true sentence end.
      Auto-enforced: `window_validator` PAYOFF_TRUNCATED (pre) + `reqc.py` FALSE-ENDING (post, `--project`)
      + `_shared/ending_check.py`. Universal — Q&A/ads/listicle/podcast/monologue. (CMO note 2026-06-17.)
- [ ] Cut name-drops / tangents with no value and reshape to land on the payoff (the "Mark Twain,
      right, said earlier" aside — cut it; payoff = "most suffering happens in our own mind").

**CAPTIONS (every clip):** spice via the active agent's authored `captions.director` stream
· height y≈58 via `captions.preset`+`no_layout:true` (NOT the ~50% layout-analyzer default) · 4K · the
render stage RE-TRANSCRIBES the cut audio, so captions can't be fixed by editing a hand `.ass`.

**REFRAME (every clip):** per-cut single-pass (`--cut-frames`), zoom 1.4 (not source-intel's over-zoom),
from 4K master, head has room (not cut off). No seam wobble.

**MUSIC (every clip):** from `vibe_music.py`/`pick_music.py`, EVERY pick filtered through
`MUSIC_BLACKLIST.txt` (shipped Clair de Lune + Danilo Stankovic — both banned). Distinct per clip.

**MECHANICS (every clip):** 4K (2160×3840) · no stray data/bin_data stream · video==audio duration ·
no black/frozen frame · hard-end on a live frame. Then a single file-integrity check; optional requested audits follow step 9.

**INFRA before a batch:** `df -h /System/Volumes/Data` (4K caches fill it → out-of-space → corrupt
output); clear `_segments` scratch + `/tmp` media when low.

Report each clip's opener + any fix BEFORE delivery: e.g. `AFewBadDays — opens "Sometimes…" @40ms, music One More Light, 4K ✓`.

---

## THE SPINE

```
 0. SCAFFOLD        0a INTERVIEW (4Qs: brand · format+count · vibe · route) → 0b project folder + manifest + scaffolds
 0b. SOURCE INTEL   → skill: source-intel  (analyze footage BEFORE editing — faces, cameras, audio, scenes)
 1. DETECT          speaker count + camera count → pick pipeline
 2. TRANSCRIBE      word-level timestamps
 3. MINE            find clip-worthy moments
 4. PICK            hand-select boundaries → cuts.json
 5. VALIDATE        5a window_validator on cuts.json (incl. FALSE-ENDING gate)  ·  5b handoff_validator on manifest.json
 6. ITERATE         cut WIP → re-transcribe → filler/opener/dead-air gates → update cuts.json
                    (loops until cuts.json produces a clean clip; never advance dirty)
 7. SOURCES READY   confirm cuts.json + captions.ass + music in manifest.json
 8. RENDER          → skill: render  (cut · reframe · grade · captions · mix · leadfix · deliver)
 8b. RE-QC GATE     → `reqc.py <delivered.mp4> --project <clip_dir>` MUST exit 0 (Law 1) — opener/
                      double-take/lead/filler/FALSE-ENDING/mechanics on the ACTUAL delivered file. Runs
                      after EVERY render, incl. --bump. (--project lets the FALSE-ENDING gate see source
                      continuation — a cut-off sentence BLOCKS delivery, all domains.)
 8b+ FRAMING GATE   → `framing_gate.py --clip <delivered.mp4>` (face-too-wide) — ported from /edit.
                      Pairs with `source-intel/scripts/shot_check.py` at PICK time (rule 0:
                      profile / off-angle / multi-person). Together they stop the "great line,
                      bad shot" clip. One-shot battery: `qa_gauntlet.py --clip <mp4> --project <dir>`
                      runs editorial + prebuild + framing_gate + reqc and BLOCKS on any fail.
 8c. NON-NEGOTIABLES → `non_negotiables_check.py --project <clip_dir>` MUST exit 0 — code-enforced
                      audit of the 3 qa_playbook non-negotiables (audio 2-mic, split-screen face-tracked
                      for Q&A clips, captions money-compact). Blocks the all-cam=speaker shortcut on a
                      Q&A clip + missing speaker_mics + caption_lint money errors. Monologue clips
                      pass via documented exception. Hard gate before audit fan-out.
 9. OPTIONAL AUDIT  → only when requested; active agent applies the bundled rubric.
                      No external model call or separate model key is required for normal delivery.
10. CONFIRM DELIVERY  20_DELIVER/v<N>/ exists; show user; await your review tool permission
                      Optional: `python3 ${CLAUDE_PLUGIN_ROOT}/skills/edit/scripts/contact_sheet.py 20_DELIVER/v<N>/`
                      emits CONTACT_SHEET.jpg (4-col grid of all thumbs, Q&A gold-labeled,
                      monologues white-labeled) so the human can scan the whole batch at a glance.
```

**Revisions (THE THREE LAWS apply — see top of file):** edit the source file for the affected layer,
re-run `engine.py <project> --bump`, **then re-run step 8b `reqc.py` AND the relevant step-9 audits on
the new delivered file** — a revision is a render, and every render re-runs the gate. Never spot-check
just the changed layer; never mechanically batch-move boundaries; verify the fix landed on the output.
The render engine's content-hash cache skips every unchanged stage and only rebuilds from the
changed one forward.

---

## Step 0 — SCAFFOLD

### 0a. PREFLIGHT INTERVIEW (hard rule — 2026-06-12)
Before running `new_project.sh`, INTERVIEW the user with 4 targeted questions. ASK them
explicitly — do NOT infer from the Drive link, the filename, or memory. Inferring is exactly
how wrong-folder / wrong-vibe misfires happen that only get caught at delivery.

```
1. Brand brand?                (speaker [default] · creator · creator · creator · _PROJECTS)
2. Format & target count?    (Q&A · hotline · listicle · podcast · monologue · multicam) × how many clips?
3. Vibe / energy?            (calm-reflective · confident-bold · playful · uplifting · cool-modern · tough-love)
4. Delivery route?           (LOCAL_ONLY by default · SPEAKER_FRAME requires explicit "push" per file per session)
```

**Skip the interview ONLY if** the user's same-message request already answered all 4
unambiguously (e.g. "cut 5 Speaker listicle shorts from this Studio link, calm vibe, deliver
local"). When in doubt, ASK — one round of 4 questions beats a wrong-brand render. Save the
answers to `_project.md` so the rest of the pipeline reads them instead of re-guessing.

**Counter-cost (don't over-interview):** Don't ask if the answer is in the message. Don't ask
if the same brand was answered in the prior turn of this conversation (the answer carries
forward inside ONE session). Don't pad with optional questions — these 4 are load-bearing,
nothing else.

### 0b. SCAFFOLD

```bash
# 1) Project folders
bash ${CLAUDE_PLUGIN_ROOT}/vault/scripts/new_project.sh <brand> <slug>
# → ~/Downloads/<brand>/YYYY-MM-DD_<slug>/  (00_SOURCE/ 10_WORK/ 20_DELIVER/)

# 2) INGEST the footage into the project's 00_SOURCE/ — three input kinds:
#    • Local file   → copy it in (NEVER edit the original):
#         cp "<path-to-file>" "<proj>/00_SOURCE/"
#    • YouTube URL  → yt-dlp (best mp4) straight into 00_SOURCE/:
#         yt-dlp -f 'bv*+ba/b' --merge-output-format mp4 \
#                -o "<proj>/00_SOURCE/<slug>.mp4" "<youtube-url>"
#    • Google Drive → use skill: footage-fetch (scaffolds its OWN verified project):
#         bash ${CLAUDE_PLUGIN_ROOT}/skills/footage-fetch/scripts/gdrive_pull.sh <brand> "<drive-url>" <slug>
#    A bare `/edit <url>` => do this ingest FIRST, then proceed. Each separate source = its own project.

# 3) Scaffold manifest.json + cuts.json + captions.ass templates
python3 ${CLAUDE_PLUGIN_ROOT}/skills/render/scripts/init_manifest.py \
    ~/Downloads/<brand>/YYYY-MM-DD_<slug> \
    --pipeline {listicle|qa|single}
```
The scaffolder auto-detects the 4K master under `00_SOURCE/`, picks a default reframe preset
for the chosen pipeline, and drops a `cuts.json` template + empty `captions.ass` for you to
populate during the MINE / PICK / CAPTION steps. Final output filename (`output.name` in
manifest.json) follows Brand nomenclature.

---

## Step 0b — SOURCE INTEL

**→ Use skill: `source-intel`** — analyze the footage BEFORE any editing decisions.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/source-intel/scripts/analyze.py \
    --source 00_SOURCE/video.mp4 \
    --out 10_WORK/source_intel.json
```

This produces a `source_intel.json` with:
- **Faces:** count, positions, sizes (tight/medium/wide/extreme_wide), clustering
- **Cameras:** scene count, cut frequency, whether it's multicam
- **Audio:** volume levels, noise floor, 60Hz hum detection, clipping
- **Recommendations:** reframe preset, zoom level, audio treatment, caption positioning

**How this feeds the pipeline:**
- Step 1 (DETECT): speaker count + camera count come from source_intel instead of guessing
- Step 4 (PICK): face size tells you if wide-stage zoom override is needed (4PRE-b)
- Step 6 (CUT): audio analysis tells you if notch filtering is needed before cutting
- Step 8 (RENDER): reframe preset + zoom come from recommendations

If `source_intel.json` exists, SKIP manual probing in step 1 — use the data. If the footage
is very long (>30min), use `--sample-interval 30` to speed up the analysis.

---

## Step 1 — DETECT

Probe the inputs. Two questions:

**How many speakers?**
- 1 video, no separate lav → **1**
- 2+ lav WAVs, or EDL with guest segments → **2**
- Ambiguous → **ASK**

**How many cameras?**
- 1 video file → **1**
- 2+ video streams / files → **multi**
- Ambiguous → **ASK**

**What changes:**

| | 1 speaker | 2 speakers |
|---|---|---|
| Diarization | none | speaker-tag via EDL or mic-bleed |
| Caption color | all white | host=white, guest=yellow |

| | 1 camera | multicam |
|---|---|---|
| Reframe | single face track | `--scene-split` per-segment |
| Boundaries | word-level | EDL segments DISJOINT in source time |

Everything else is identical.

---

## Step 1b — LOAD MAP (read these for THIS job, skip the rest)

Don't read every reference doc every time. Match the job to its row, open ONLY those files,
ignore the others. (Routing pattern: read these / skip these / maybe-load these skills — keeps
context lean instead of loading all 14 reference docs + every sub-skill on every clip.)

| Job type | READ (`references/`) | SKIP | Skills to load |
|---|---|---|---|
| Q&A / Hotline | **qa_playbook** (THE one-shot procedure — read first), **qa_clip_patterns** (rules+data), editorial_sop, assembly_cut_standard, QA_HOTLINE_SOP, hooks_taxonomy, subtitle_rules, ui_safe_zone | segment_select_tighten | horizontal-to-vertical (stage / split-top / guest), caption-clips, sf-audit, scorecard-audit |
| Podcast (2-person) | segment_select_tighten, assembly_cut_standard, hooks_taxonomy, **longform_clip_patterns §6 (podcast FLIPS: bold_claim opens win, weld underperforms, interior-trim is fine)** | editorial_sop, QA_HOTLINE_SOP | horizontal-to-vertical (podcast / guest + split), caption-clips, both audits · select via **`clip_select.py --rules config/clip_lift_podcast.json`** |
| Monologue / listicle | hooks_taxonomy, visual_guide, subtitle_rules, **longform_clip_patterns** | Q&A + podcast SOPs | listicle-short OR this spine, horizontal-to-vertical (talking-head), caption-clips |
| Multicam (any) | assembly_cut_standard, qa_sop_locked | — | `scripts/qa_assembly.py` path |
| **Testimonial ADS (paid social)** | **`testimonial_ads` (READ FIRST)** | Q&A/podcast SOPs | horizontal-to-vertical (**2D face-pin** `_shared/testimonial_reframe.py` — NOT vidstab / NOT qa_reframe for shaky multi-person), caption-clips (spice + `bubble` preset), `/promo` (animated end-card CTA). **NO music · burn disclaimer every clip · LOCAL delivery only** |

`CLIP_CUTTING_PLAYBOOK.md`, `references/ffmpeg.md`, and **`references/clip_creation_rules.md`** apply
to every job (the last is the no-revisions creation checklist — read it every clip). When the job type
is ambiguous, ASK before loading (same rule as DETECT).

---

## Step 2 — TRANSCRIBE

→ Use skill: `long-form-ingest` for AssemblyAI-first source transcription and the Gemini delivery map.
Reuse its source-ordered words, speaker labels and provider evidence. `GROQ_API_KEY` is for
fallback/audit helpers; it does not replace AssemblyAI onboarding. Local Whisper is an explicit
fallback, not an equivalent primary source transcript.

---

## Step 3 — MINE (two passes)

### 3a. Active-agent source reading and selection

Read the full source transcript and the format's bundled editorial references from Step 1b.
The current model identifies promising interactions, reads their complete context, and designs
exact spoken hook → reasoning/story → payoff cuts. Write the normal source-linked candidate,
word-selection and cut artifacts yourself. Use local scripts for deterministic mechanics only.
Do not launch `thread_mine.py`, `clip_select.py`, `tam_select.py`, or another model API to make
these decisions, and do not ask the editor for a model API key. The docs are the editorial brain;
the active agent is the model applying them.

### 3d. DATA-BACKED ENTRY SIGNALS (longform_clip_patterns, updated 2026-06-14)
From **602 raw→clip pairs** (lift = winrate ÷ 0.25 base; >1 = real edge). Prioritize moments you can OPEN as:
- **Cut-to-payoff** (lift 1.68): open ON the punch, skip the setup — the best open
- **Extreme number** (1.43): lead with the striking quantity/stat
- **Clean source open kept** (1.43): if the source already opens cold on the thesis, KEEP it
- **Direct address** (1.28): name the viewer's situation ("if you have less than $100K…")
- bold_claim / anecdote = **NEUTRAL** (0.96 / 0.97) — fine, but pair with a number/stakes/vehicle; not an edge alone
- 🛑 **Question opens = AVOID** (lift 0.18, the strongest negative) — convert to the claim it implies
**RELOCATE the best buried line to the front** (weld, lift 1.33) when the source buries it — but
DON'T reach past an already-clean open. Always strip preamble/throat-clear before the hook.
→ Full lift tables + the myths this corrects: `references/longform_clip_patterns.md`

For Q&A with separate mics: read BOTH transcripts (host lav + guest mic) to find where each
speaker's audio is cleanest. Guest intro/questions often only exist clearly on the guest mic.

---

## Step 4 — PICK (hand-cut, NEVER auto-batch)

Choose the EXACT hook + payoff PHRASE for each candidate. Anchor WORD-PRECISE to the transcript.

### Universal rules:
0. **🛑 VERIFY THE SHOT — run `shot_check.py` on EVERY candidate before you commit it.** Good words
   on a bad shot = a bad clip.
   `python3 ${CLAUDE_PLUGIN_ROOT}/skills/source-intel/scripts/shot_check.py SOURCE --start <in> --end <out>`
   Only ship windows that return **GOOD**. If it returns **PROFILE / WIDE / MULTI / NO_FACE**, the
   moment looks bad as a vertical no matter how good the line is — find a different window where the
   SAME point is on a face-on, single-subject shot, or drop it. This is the #1 selection guard — it's
   what stops "great quote, but the speaker's in side-profile / on the wrong cam." MANDATORY on
   multi-cam / podcast / interview sources (where the camera cuts between people and angles).
1. **CONTEXT FIRST** — commentary/answer clip MUST include the question/story it responds to
2. **HAND-CUT** — never let an LLM batch-pick timestamps
3. **Hook = first content word** — not a filler ("and", "so", "um", "yeah", "cool", "okay", "right", "because")
4. **Payoff = last complete sentence** — never mid-clause (EXCEPTION: mid-sentence cliffhanger
   cuts ARE valid when the title carries the payoff — see `longform_clip_patterns.md` exit rules)
5. **HARD END on the payoff** — no fade, no frozen frame
6. **Duration:** target **20–45s** shorts (60–75s mids, 90s HARD CAP). **Min ~15s** — a clip must
   carry a full **hook → context → payoff**, NOT just the punchline. A ~9s one-liner with no setup
   reads as a fragment; extend the in-point back to include the setup that makes the payoff land.

### 4-DATA. DATA-BACKED EXIT + INTERIOR + STRUCTURE RULES (longform_clip_patterns, updated 2026-06-14)
From **602 raw→clip pairs** (lift vs 0.25 base). Verified by a 5-lens adversarial panel that
corrected prevalence-as-endorsement myths (the old 8-clip rules were wrong in 3 places).
**EXITS (where to end):**
- ✅ END ON THE PUNCHLINE / EMOTIONAL PEAK (lift 1.61) — the #1 exit edge.
- ✅ A COMPLETE punchy sentence (1.39) or a short IMPERATIVE button (1.33) also win.
- ⚪ "Principle"/aphorism is NEUTRAL (0.89) despite being 57% of clips — don't optimize to land on a maxim.
- 🛑 NEVER end on a truncation-before-the-explanation (lift 0.35, the WORST exit). End ON the peak/complete
  line, not before the "why". (Cutting the abstract "why" from the INTERIOR is fine; ENDING there is not.)
**INTERIOR (what to remove):** tangents (53% of clips) → redundant restatements (51%) → framework
scaffold (29%) → false-starts / discourse markers (29%) → preamble/throat-clear → the abstract "why"
(keep the concrete vehicle) → weak 2nd example → CTA. Compress multi-turn dialogue to setup+result.
**STRUCTURE (the big one):** ✅ front-trim (1.48) / weld-one-arc-to-front (1.33) / verbatim-lift (1.16)
WIN; 🛑 heavy interior-trims is the default but UNDERPERFORMS (0.72). If you're making many interior cuts
to rescue a chunk, pick a cleaner arc or WELD one instead. MATCH SURGERY TO SOURCE: tight take → ship
~verbatim; sprawling talk → isolate ONE arc and discard the rest (isolate, don't dilute).
**KEEP the concrete vehicle** (story/number/worked example); cut the abstract framework around it. SHOW, don't tell.
→ Full lift tables + anti-patterns: `references/longform_clip_patterns.md`

### 4PRE. FILLER SCAN AT BOUNDARY TIME (hard rule — 2026-06-11)
**Before extracting any audio**, scan the word-level transcript at EVERY segment boundary for
filler words. This catches fillers BEFORE they enter the assembly — not after.

For each segment's start/end time, check the 3 nearest words:
- **Start of segment:** If the first 1-3 words are fillers (um, uh, so, cool, okay, yeah,
  right, because, well, and, but, like-as-filler), MOVE the start time FORWARD past them to
  the first content word.
- **End of segment:** If the last 1-2 words are fillers (um, uh, like, yeah, okay, right),
  MOVE the end time BACKWARD before them.
- **Within segment:** Scan ALL words inside each segment. Any filler word = either split the
  segment around it or note it for removal in the cut stage.

**Why at boundary time, not post-render:** Fillers at segment edges are invisible in the
transcript selection but become audible in the mix. "We do 55 million" looks clean in text,
but if the audio starts 0.5s before "We" with a "Cool. Uh," you ship a filler. Scanning
word timestamps at boundary time is the ONLY reliable way to catch these. (Learned 2026-06-11:
shipped "Cool. Uh", "Um", "So", "Okay so" — all at segment edges that looked clean in text.)

**Counter-cost (don't over-trim):** A short "So" or "Well" that's part of natural cadence at
the START of an answer can be fine and even *humanize* the opening — trim it ONLY when it's a
true verbal-tic filler (long, drawn out, hedging) or when it precedes the real hook word. If
removing the leading word makes the opener feel chopped/abrupt, leave it. The goal is "no
fillers shipped," NOT "every connective word ripped out." When in doubt, listen — don't trim
by transcript alone.

### 4PRE-b. WHISPER TRUE_END PADDING (hard rule — 2026-06-11)
Whisper's word-end timestamps are 0.1–0.25s EARLY — they mark where the vowel nucleus ends,
not where trailing consonants finish. Words ending in soft consonants (-s, -t, -th, -m, -ng,
-tion) get clipped if you cut at Whisper's label.

**When setting segment END boundaries from Whisper timestamps:**
- Add **+0.15 to +0.25s** past Whisper's word-end for any word ending in a soft consonant
- Verify by checking the audio waveform: the actual silence (not just Whisper's label) should
  be AFTER your end point
- If in doubt, use the NEXT word's start time as your ceiling — better to include 0.1s of
  silence than clip the last consonant

(Learned 2026-06-11: "them" was cut at Whisper's 2830.20 label, losing the "-m" consonant.
The true acoustic end was at ~2830.40. Reverted to 2830.45 — "them" became audible again.)

**Counter-cost (don't over-pad):** Padding more than ~0.30s past Whisper's word-end risks
catching the FIRST phoneme of the next word — that's an audible "th-" or "s-" leak at the
splice. Cap padding at +0.25s; if you need more, use the next word's start time as a hard
ceiling and verify with the waveform. Better to lose 0.05s of trailing consonant than to
ship a leading-phoneme leak from the next sentence.

### 4PRE-c. WIDE-STAGE CAM FRAMING CHECK (2026-06-11)
When using a wide stage camera where the subject is full-body in frame (face <1% of frame
area), the default `stage` zoom (1.6) produces a BODY shot, not a face shot — the
subject's head is at the top edge or cut off. **Check the face size in the first frame:**
- Face area >2% of frame → default zoom is fine (chest-up shot)
- Face area 0.5-2% → use zoom 2.0-2.5 (medium stage shot)
- Face area <0.5% → use zoom 2.5-3.0 (wide stage shot) — quality will degrade; consider
  whether a different camera angle is available
Probe with: extract one frame → YuNet face detect → check `face_w * face_h / (frame_w * frame_h)`.
(Learned 2026-06-11: shipped a body-framed Speaker at zoom 1.6 on a wide stage cam where his
face was 0.2% of frame. Zoom 2.4 fixed it to head-and-shoulders.)

### 4PRE-d. BOTH-MIC BOUNDARY SCAN (hard rule — 2026-06-11)

When mixing two mics (host lav + guest mic) for assembly cuts, the NON-ACTIVE speaker's
interjections ("cool", "amazing", "sick", "right", mid-sentence speech) bleed through at
segment boundaries. **Before finalizing ANY segment boundary, transcribe a ±1s window on
BOTH mics around the cut point.** Check for:

1. **Non-active speaker has speech within 300ms of the boundary.** If so:
   - Adjust the boundary to clear the speech (move end earlier / start later), OR
   - Use SINGLE-MIC for that segment (only the active speaker's mic — eliminates bleed)
2. **ANY word onset within 100ms AFTER the end boundary.** The 50ms fade-out won't fully
   mask a word starting that close. Move the end earlier to create ≥150ms of silence after
   the last desired word before the next word onset on EITHER mic.
3. **Mid-sentence speech on the non-active mic at the cut point.** Chopping through someone
   mid-word creates an audible edit artifact even if the level check passes. Move the
   boundary to a silence window on BOTH mics.

**Single-mic fallback:** When a guest-only segment has no host speech needed (e.g. guest
intro, guest monologue), using guest mic alone eliminates all host-mic bleed. The room tone
shift is minimal for segments >1s and inaudible under speech. Use this for any segment
where the non-active speaker's interjections can't be cleared by timing alone.

(Learned 2026-06-11: V10 shipped Speaker's "cool" bleeding through the mixed audio at the
seg 0→1 splice — Speaker was saying "Cool" on his lav at 2682.46, overlapping with the
segment end at 2682.44. Also shipped Speaker's "Amazing" and "Based on contract" chopped
mid-sentence at the seg 1→2 boundary — set the boundary based on TOM's speech without
checking what SPEAKER was doing on his lav. Both fixed in V11 by checking both mics at
every boundary.)

**Counter-cost (don't kill all room tone):** Single-mic fallback eliminates bleed but also
strips the natural "we're in a room together" feel. Use it when the bleed is a clear
artifact (mid-word chop, audible interjection at the splice); DON'T default to single-mic
for every Q&A segment — light, distant non-active speaker presence (breath, paper rustle,
quiet "mhm" >1s from boundary) reads as natural ambience and shouldn't be scrubbed. Mix
unless the bleed is failing the gate.

### 4PRE-e. ECHO/RESTATEMENT FILTER (editorial rule — 2026-06-11)

In Q&A/hotline clips, the guest often echoes or restates the host's question before
answering. These lines are dead weight in short-form — they slow the pace and add nothing
the viewer doesn't already know.

**Flag for removal any line where the guest:**
- Repeats the host's question back verbatim ("To scale my back end?")
- Restates/paraphrases the question ("Like service delivery?")
- Opens with "Because yes," or "So basically," echoing prior context
- Confirms something already established ("Yes, I could buy them" after already saying "Yes")

**Keep echo lines ONLY when they add new information** (e.g. "To scale the back end? We
actually tried that — it cost us $2M" adds the cost detail). If the echo is pure
restatement, cut it.

The flow should be: Host asks → Guest ANSWERS. Not: Host asks → Guest restates question →
Guest answers. The viewer heard the question; they don't need it twice.

(Learned 2026-06-11: V10 shipped "Because yes, I could buy them" — redundant after Tom
already confirmed "Yes" — and "To scale my back end? Like service delivery?" — pure
restatement of Speaker's question. Both cut in V11. These should have been flagged during
initial selection at step 4, not caught in human review.)

**Counter-cost (the brand person needs SOMETHING to react to):** Cutting EVERY guest echo
can leave the brand person's answer floating with no setup ("So you should buy the
companies above you" — buy WHICH companies?). When the host's question was long/specific
and the guest's restatement is the ONLY in-clip statement of what they're answering, KEEP
a 1-2 second restatement so the cold viewer (4a) understands the question. Cut pure
filler-echoes; keep echoes that are the clip's only context anchor.

### 4a. COLD VIEWER TEST (hard rule — 2026-06-11)
The viewer comes in with ZERO context. They haven't read the transcript. They haven't seen
the prior 20 minutes. **The script IS all the context they get.**

After selecting lines, re-read every one as a cold viewer who just opened this video:
- For each line ask: "Does this make complete sense given ONLY the lines above it?"
- Lines referencing "that," "this," "the focus," "what we discussed," "the same thing" =
  **CUT** unless the referent was explicitly stated earlier IN THIS CLIP
- Jargon from a prior exchange ("one avatar," "grand slam offer") that hasn't been defined
  in this clip = CUT or replace with a plain-language version
- If a line NEEDS prior context: (a) add the setup line BEFORE it, (b) replace with a
  self-contained version, or (c) cut it entirely

The LLM has the full transcript; the viewer has only the clip. Test from THEIR perspective.

**Counter-cost (don't bloat the opener):** "Add setup before it" can sprawl into a 6-second
explainer that buries the hook. Cap added context at ONE concise line; if it takes more
than that to make the payoff land, the clip is structurally wrong — pick a different
candidate, don't pile on prefix. Coherent ≠ exhaustive. The hook still has to land in the
first 1.5 seconds.

### 4b. LEAD WITH THE PAYOFF — coherent order, no Frankenstein (updated 2026-06-11)
**Open on the strongest line — NEVER bury the hook.** If the gold is later in the answer, lead the
clip THERE: select the self-contained chunk that STARTS with the payoff and drop the weak preamble.
(Proven: opening on "people are very surprised by their lives" instead of the dry analytical setup
that preceded it; replacing a jargon lead with "what used to feel good doesn't feel as good after a
hundred times.")
BUT keep it COHERENT — **never splice disjoint quotes from far-apart parts of the conversation** into
a fake arc; the viewer feels it. Moving a sentence to the front is fine ONLY if the result reads as
one natural arc that passes the cold-viewer test (4a). Frankensteining a quote from 3 minutes away
into the middle of another is still banned. One coherent arc that OPENS on its best line.

### 4b-RULES. → `references/clip_creation_rules.md` (READ + APPLY at pick time — every clip)
The recurring revision notes, baked into creation rules so the FIRST cut ships: lead-with-payoff,
cold-viewer context, **the brand person carries the clip** (not the guest), **finish the point**
(don't end early), **earn the length** (no thin 10-16s clips), **end on the brand person** (last
frame is never the interviewer), no leading fluff, kill filler first pass. Operator: "bake them in so
you don't make it again."

### 4c. WIRE HOOK TYPE INTO CAPTION CONTEXT (run once after picking)
After choosing which candidate to cut, write its `hook_type` + performance signals into
`manifest.json` so the caption director receives a clip-specific emphasis brief at render time:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/edit/scripts/set_caption_context.py \
    --tam      10_WORK/tam.json \
    --rank     <chosen rank> \
    --manifest manifest.json \
    --speakers 2   # or 1 for solo monologue
```

This writes `stages.captions.context` in `manifest.json`. The render engine forwards it to
`spice_caption.py --context` → `caption_director.py --context`. The director then knows:
- hook_type → which word in the opening line gets `peak` size
- emotion → what emotional register to emphasize
- topic → whether $ figures should be upsized (Pricing/Sales/Wealth)
- speaker setup → correct Q&A yellow/white split or solo-white

**Why this matters:** without this, the director receives a generic "the creator clip" context
and has to infer the hook type from the transcript alone. With it, a bold_claim clip instantly
gets its claim-payload word peak-sized; a question-hook clip (0.48x lift) has the director
explicitly redirected to peak the ANSWER instead of the question.

### Q&A / Hotline specifics:
**▶ WHAT makes a good Q&A — READ FIRST: `references/QA_MASTER_SOP.md`.** The consolidated editorial+visual
single-source-of-truth: the reference editor's master-editor framework (money-first hook, ONE problem, kill-your-darlings,
STATE→GIVE→PROVIDE, 6-point Value Framework, 60–90s / up-to-2min for standout long interactions) reconciled
with the 250-pair data + visual guide + every session lesson. This is the SPINE. (Same file at
`${CLAUDE_PLUGIN_ROOT}/vault/SOPs/QA_MASTER_SOP.md` for drag-drop.)
**▶ GOLD CUTS to pattern-match — `references/qa_worked_examples.md`.** Approved real cuts (Guest, Nedu, …)
with beat-by-beat why + fundamentals scores. Study these before cutting; they out-calibrate abstract rules.
**▶ HOW to execute it — `references/qa_playbook.md`.** Full ingest→deliver
steps with exact commands, decision rules, and DONE-WHEN gates so a fresh-context AI produces a
correct Q&A in one pass. The data/rules behind it: `references/qa_clip_patterns.md`.
→ Also: `references/editorial_sop.md`, `references/assembly_cut_standard.md`, `QA_HOTLINE_SOP.md`

### 4-QA-DATA. DATA-BACKED Q&A CUT RULES (qa_clip_patterns, 2026-06-14)
From 1,110 Highlights-highlight→short pairs (805 verbatim cut-diffs + 51 business cut-diffs + 48 finished clips):
- **OPEN on the guest's business intro + revenue; DROP the name** (revenue kept 65%, name dropped 92–100%). The number is the hook. Cut greetings / goal-setting preamble / disfluencies.
- **Keep ONE self-contained arc** — the cleanest problem→diagnosis→(reframe)→tactic→payoff loop that survives with ZERO prior context. Kill competing threads even if substantive.
- **EXTRACT-AND-WELD:** keep the intro, jump across the middle, relocate the best payoff line to land as the button. Clip order ≠ conversation order.
- **END on the payoff — 100% of the time.** Payoff = a portable PRINCIPLE 53% (else tactic / tough-love / number-reframe). NEVER ride into the next question or the wind-down.
- **CUT the CTA outro** ("company.com/roadmap", "free gift", "link in bio") — 96%. Discard the back ~22% of the source.
- **Compression ~9:1** (≈250s source → ≈35s short; shorts 17–60s). Preserve numbers EXACTLY; keep Speaker's crisp one-word beats ("Sure", "Good call") for pace.
- **Caption = a generalized principle**, not a verbatim clip line — abstract the takeaway above the specific case.
→ Full rules + frequencies + anti-patterns: `references/qa_clip_patterns.md`

### Podcast specifics:
→ See `references/segment_select_tighten.md`

---

## Step 5 — VALIDATE

Two validators run BEFORE any cut/encode work. Each catches a distinct failure class.

### 5a. Window validator — boundaries vs transcript
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/lib/_shared/window_validator.py \
    --transcript words.json --spec structure.json --rough --source VIDEO.mp4
```
9 automated rules: LEADING_ORPHAN, TRAILING_ORPHAN, OVERLAP, PAYOFF_TRUNCATED, OPENER_DIRTY, CLIPPED_TAIL, GHOST_SILENCE, TANGENT_RISK, DISJOINT_ORDER. Fix every ERROR before cutting.
**PAYOFF_TRUNCATED is the FALSE-ENDING gate (2026-06-17, all domains):** it now ALSO flags a CONTENT-word ending where the SAME speaker keeps going in the source (via `_shared/ending_check.py`), not just function-word endings — the "cuts off the speaker" defect. ERROR = BLOCK; never deliver a cut-off sentence.

### 5b. Handoff validator — manifest vs sub-skill contracts (2026-06-12)
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/lib/_shared/handoff_validator.py <project_dir>
```
Validates the **full orchestrator→sub-skill payload** before any sub-skill (render, h2v,
caption-clips, mix) runs. Catches the "orchestrator forgot to set X" / "X has the wrong
type" class — which Anthropic's prompting playbook flags as the #1 multi-agent failure
mode. Rules:
- top-level: `title`, `pipeline` (∈ {listicle, qa, single, podcast, multicam}), `output.name` (Brand format `.mp4`)
- `cut`: `source_video` + `spec` exist; cuts.json parses; segments have valid `in<out`; total duration 8–90s; last segment is a real sentence terminator
- `reframe`: preset ∈ {talking-head, stage, split-top, guest, podcast}; zoom in [0.8, 3.5]; res ∈ {1080, 4k}
- `captions`: speakers count matches pipeline (1 for listicle/single, 2 for qa/podcast/multicam — wrong count = wrong host/guest color split)
- `mix`: music exists on disk AND is under `(1) Tik Tok/` AND not in `MUSIC_BLACKLIST.txt`; music_lufs ≥ voice_lufs−14
- qa/podcast/multicam pipelines additionally require `cut.source_audio` (lav)

Exit 0 = clean (proceed). Exit 1 = errors (BLOCK render; fix the manifest). Exit 2 =
warnings only (proceed, but eyeball). Run this every clip — running the validators here
is what stops "render finished, audit failed, revise V2" cycles caused by upstream config
drift.

---

## Step 6 — CUT + CLEAN (filler removal is NOT optional)

### 6a. MIC ROUTING (2-speaker Q&A — 2026-06-11)
When you have separate mics (host lav + guest mic), **MIX BOTH MICS for every segment**
using `amix=inputs=2:duration=shortest:normalize=0`. Do NOT switch between mics per speaker —
that creates an audible room-tone / reverb mismatch at every speaker change (sounds like an
echo). Mixing both mics keeps consistent room tone throughout AND ensures both speakers are
clearly audible: each mic picks up its wearer at full level and the other speaker from natural
room pickup. After mixing, per-segment loudnorm to a consistent target (e.g. -24dB mean or
-16 LUFS) so neither speaker's segments are louder than the other's.

**Why not per-speaker mic switching:** Tried 2026-06-11 on a Q&A assembly — the guest was
inaudible on the host's lav during back-and-forth dialogue, and switching to guest-only made
the host inaudible. Mixing both solved it: both speakers clear, no echo, consistent tone.

### 6b. Content cut
Choose one engine:

**precision_cut.py** (default, fast):
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/lib/_shared/precision_cut.py \
    --source VIDEO.mp4 --transcript words.json --keep-spans '[[start, end]]' --out clip.mp4
```
> 🛑 **Cut WITH this engine — do NOT hand-roll a bare `ffmpeg trim+concat`.** precision_cut.py fades every
> seam (~6ms) and true-ends each word; a raw concat leaves a **click/pop at every seam** (shipped one
> 2026-06-11). If you ever must concat manually, put an `afade` on each segment edge or you WILL get pops.
>
> 🛑 **ASSEMBLY CUTS (multi-segment, dual-mic mixed) need 50ms fades, NOT 6-8ms.**
> When mixing two mics (host lav + guest mic) per segment and concatenating, crosstalk from the
> non-speaking mic extends past the speaking mic's word boundary. At 8ms the crosstalk is still
> at full level when the fade hits — the ear hears a pop/click at every splice. At 50ms the
> crossfade is long enough to mask the crosstalk tail. Use `afade=t=in:st=0:d=0.050` and
> `afade=t=out:st={dur-0.050}:d=0.050` on EVERY segment's audio before concat.
> (Learned 2026-06-11: V8 used 8ms fades and had audible pops at 5 of 11 splice points.
> V9 switched to 50ms — zero pops across all 12 splices.)

**script-cut** (MFA precision, when transcript timestamps are unreliable):
```bash
"$HOME/.local/share/script-cut/venv/bin/python" \
    ${CLAUDE_PLUGIN_ROOT}/skills/script-cut/scripts/script_cut.py \
    --source VIDEO.mp4 --transcript words.json --spec structure.json --out OUTDIR/
```

### 6c. 🛑 FILLER SURGERY — BLOCKING (a clip with fillers NEVER gets shown to the user)

**This is not a suggestion. This is a gate. A clip that contains ANY filler word does NOT
move to step 7. It stays here until it's clean.**

After the content cut, transcribe the cut clip and scan for:
- **um, uh, ah, mm, mmhmm, mhm** — always fillers, always remove
- **like** (when not comparing: "like a" / "like the" / "like when" = filler) — remove
- **you know, I mean, right** (discourse markers, not content) — remove
- **so, and, but, well, yeah, okay** (at sentence start or as standalone) — remove from opener, evaluate in body
- **repeated words/phrases** ("I think I think", "the the") — remove the duplicate

**How to remove each filler:**
1. Get the filler word's `start` and `end` timestamps from the transcript
2. Build keep-spans that SKIP the filler: `[..., prev_word.end, filler.start], [filler.end, next_word.start, ...]`
3. Re-cut with **precision_cut.py** using the new keep-spans (it fades the seams — a bare `ffmpeg trim+concat` pops)
4. Re-transcribe the result and scan AGAIN — removing one filler can reveal another

**Loop until the transcript is CLEAN. There is no "good enough."**

### 6d. 🛑 DEAD-AIR REMOVAL — MOTION-GATED (see LAW 4; blindly stripping pauses jump-cuts)

> ⚠️ **DO NOT blindly run `jumpcut.py --max-pause 0.15` on single-cam talking-head/gesturing footage.**
> jumpcut removes every pause; on a single locked camera where the subject moves during a pause
> (drawing, gesturing, leaning), removing it teleports his body → a JUMP CUT. This shipped 61 times in
> one batch (2026-06-12). Aggressive dead-air stripping is for MULTI-cam (the angle change masks it) or
> footage where the subject is near-stationary — NOT single-cam gesturing.

**Single-cam (talking-head/desk) dead-air policy:**
- KEEP within-take pauses CONTINUOUS by default — they read as natural cadence, and on draw/gesture
  footage the pause IS visual content. (Matches 7c + LAW 4.)
- Trim a pure-silence pause ONLY if it is BOTH egregious (>~1.5s of true silence) AND low-motion across
  it (extract a frame ±0.15s of the would-be cut, full-frame grey Δ ≤ ~14 → the cut is invisible).
  Otherwise leave it.
- NEVER "split a multi-second pause inside one span" reflexively — that is the jump. If the pause is
  long AND high-motion, keep it continuous (or pick a tighter span that doesn't contain it).

**Multi-cam / near-stationary single-cam:**
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/caption-clips/scripts/jumpcut.py clip.mp4 out.mp4 --noise <floor> --max-pause 0.15
```
- Floor = `mean_volume − 7 to −10 dB` (MEASURE with `volumedetect`, don't guess)
- Run jumpcut on the RAW audio (before loudnorm), not after — loudnorm lifts room tone above threshold

**After ANY dead-air work, the step-8b `reqc.py --project` JUMP-CUT gate is the backstop** — it flags
pure-silence same-take seams with cross-seam Δ>14 and BLOCKS delivery. If it fires, you stripped a
pause you shouldn't have — merge those two segments back to continuous, don't try to "clean the cut."

### 6e. Content-level trim

The active agent reads the selected transcript, removes unsupported/redundant spans from its
source-linked plan, and checks the spoken arc against the bundled cutting playbook. Do not
launch `llm_edit.py` or a second model to make these editorial decisions.

---

## Step 7 — QC PRE-RENDER (BLOCKING — clip does NOT leave this step until all pass)

> **The user must NEVER see a clip with fillers, long pauses, or a weak opener.
> These checks exist because that happened and it wastes the user's time.
> A clip that fails ANY check goes back to step 6, not forward to step 8.**

### 🔒 FOR Q&A CLIPS: run `qa_prebuild_audit.py` BEFORE step 8 (2026-06-16)
For any clip going through `qa_assembly.py` (split-screen + cam-switch Q&A grammar), the 7-gate `qa_prebuild_audit` is **mandatory** between EDL design and 4K encode. It catches the defect classes Operator reviewed Guest for (zero-gap word-clip, cam-transition cuts mid-completion, ends-before-payoff, no problem in intro, slow music intro, guest panel too low). Saves a 7-min wasted encode per flagged defect.
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/edit/scripts/qa_prebuild_audit.py <clip_dir> \
    --transcript <words.json> --sync <qa_sync.json> --music <track.mp3>
```
Exit 0 → encode. Exit 1 → fix flagged items (audit prints `suggested_fix` per flag) and re-audit. **See `references/qa_playbook.md` STEP 8a for what each gate checks.**

### 7a. FILLER GATE (hard block)
Transcribe the final cut clip. Search the FULL transcript for:
`um, uh, ah, mm, mmhmm, mhm, like (filler), you know, I mean, right (filler), okay (filler)`

- **ANY filler found anywhere in the clip = FAIL.** Go back to 6b, remove it, re-check.
- Do NOT proceed. Do NOT show it to the user. Do NOT say "there's a small um at 0:23."
  Fix it first, then move on.

### 7b. OPENER GATE (hard block)
The first audible word of the clip must be a strong content word. Check the first 3 words:
- **FAIL if first word is:** and, so, but, um, uh, like, yeah, well, okay, right, also, I mean, you know, basically, actually, honestly, literally
- **FAIL if opener is a sentence fragment** (continuation from a prior sentence — e.g. starts with "...her ex" from a question the clip doesn't include)
- **FAIL if opener doesn't match the intended hook phrase** from mining

On fail → go back to step 6, re-cut with a tighter in-point or one sentence later.

### 7c. DEAD-AIR GATE (hard block) — but DON'T strip natural pacing
Two different things; treat them differently (learned the hard way 2026-06-11):
- **Dead air AT a cut/seam, or a held/frozen frame at a transition** = the defect. The "dead space that's
  almost a jump cut." REMOVE it (tight seam, true-end the outgoing word, true-onset the incoming word).
- **A natural pause WITHIN one continuous take** (the speaker's own cadence) = KEEP. Do **NOT** wholesale-strip
  every pause to make it "tight" — that makes it choppy/rushed and is an over-correction the user will reject.
  Only tighten a within-take pause if it's egregious (>~0.6s of true silence) or the user explicitly asks.
- Let the cut engine do seam silence + fades (precision_cut.py) — don't hand-trim every gap.

### 7d. HEAD-TRIM GATE (hard block — 2026-06-11)
The first audible speech must begin within 50ms of frame 1. Measure:
```
ffmpeg -i clip.mp4 -t 0.5 -af "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=/dev/stdout" -f null -
```
If speech onset (first frame above -28dB) is >50ms from start, trim the head with `-ss <onset-30ms>`.
Dead air at the top of a clip = immediate scroll-past. This gate catches extraction misalignment
where the segment start time is before the actual word onset. (Learned 2026-06-11: shipped 0.5s
of dead air before the hook — the segment timestamp was on the word's Whisper label, but the
actual acoustic onset was 0.5s later.)

### 7e. DURATION + CONTENT
- No dead air, breath, or non-verbal at clip start
- Video duration == audio duration (no stray data/timecode/bin_data streams)
- Clip ends on a live frame, not black or frozen
- Hook phrase actually present in the audio (not just in the cut_spec)

### 7f. ASR BLIND SPOT
ASR re-reads clipped words as whole — NEVER trust a transcript to confirm a word is intact.
Verify by: word duration (real word = 0.2–0.5s), spectrogram (complete word tapers, clipped word has hard edge), energy envelope.

### 7g. POP + DEAD-FRAME + DEAD-BEAT SCREEN (hard block) — all auto-detected by `sf-audit`
Defects that shipped 2026-06-11 because nothing screened for them. All are automated checks in
`sf-audit/scripts/audit.py` (#12 pops, #15b frozen frames, #15c dead beat after a cut) — run the audit and these gate delivery:
- **Audio pop/click at a seam** → caused by a BARE hard-concat cut (waveform discontinuity). **Never hand-roll a
  raw ffmpeg `trim+concat`** for the content cut — it clicks at every seam. Use `_shared/precision_cut.py`
  (it fades every seam + true-ends each word). If you must concat manually, use 50ms `afade` for assembly
  cuts with mixed mics (see 6b), or 6ms for single-source cuts.

**7g-VERIFY. SPLICE-POINT AUDIO VERIFICATION (mandatory — 2026-06-11)**
After building ANY assembly cut, extract the audio and check the waveform at EVERY concat point:
1. Calculate each splice's clip-time position (cumulative segment durations, minus head trim)
2. At each splice, compare the mean absolute level of the 10 samples before vs after the splice
3. If the ratio (max/min) exceeds 10x → that splice has a pop. Fix it (increase fade, adjust boundary)
4. Log every splice's ratio. Zero pops = pass; any pop = fail, do NOT deliver.

**Level-clean ≠ content-clean.** A splice can pass the 10x ratio check but still sound bad if
it contains unwanted speech from the non-active mic being chopped mid-word. Level checks catch
pops; they do NOT catch content bleed. The real fix is upstream: **4PRE-d** (both-mic boundary
scan) prevents content bleed from entering the assembly in the first place. If a reviewer flags
a "bad audio cut" that passed the level check, the root cause is almost always a 4PRE-d
violation — speech on the non-active mic at the cut point.

(Learned 2026-06-11: V8 had 5 audible pops caught by level checks. V10 had 0 pops but STILL
had 2 bad audio cuts — Speaker's "cool" and "Based on contract" chopped at boundaries. Level
checks said PASS. The problem was content bleed from the non-active mic, not level
discontinuity. Added 4PRE-d to catch this class of error at boundary-setting time.)
- **Frozen / held (dead) frame** → a duplicate frame at a transition (e.g. the reframer "holding" a crop when
  a face isn't detected on the cut frame). `qa_reframe_v2.py` no longer holds across a scene cut (fixed
  2026-06-11) — the per-segment interpolated position is the correct new angle. If a held frame still slips
  through, remove those frames. blackdetect (#15) only catches BLACK frames; #15b catches non-black holds.
- **Dead beat AFTER a camera cut** → the cut lands on a live-but-SILENT frame (a speaker who hasn't started
  the line yet), so the new angle just sits there before the words begin. This is NOT a frozen frame
  (#15b/freezedetect won't catch it — the frames are real, just silent), it's silence on a fresh angle.
  #15c finds it via cv2 cut-detection + a music-robust voice-rise test (a music bed makes the gap non-silent,
  so it measures how long until voice resumes relative to the line that follows). Fix = trim the gap so the
  cut hits speech (the camera switch lands on the first word). Shipped on a Q→answer cut 2026-06-11 ("you
  should have screened for this"). WARN, not hard fail — a held beat is occasionally intentional pacing
  (per 7c, don't strip natural pacing); the eye confirms which flagged beats to tighten.

### 7h. AI VERIFICATION — NEVER DISMISS DISCREPANCIES (hard rule — 2026-06-11)
When using Gemini/watch to verify a built clip, treat EVERY discrepancy as real until you
have positive proof it's a false alarm. Specifically:
- If Gemini transcribes a word that shouldn't be there (filler, crosstalk) → check the audio
- If Gemini says a word sounds cut off → check the waveform at that timestamp
- If Gemini flags something you "already fixed" → the fix may not have landed; verify
- **NEVER rationalize a discrepancy away** ("Gemini is being loose", "that's just how it
  transcribes it", "it's probably fine"). That rationalization shipped 5 bugs in V8.
- Check ALL boundaries, not just the ones you thought were risky. V8 only checked 4 of 24
  boundaries for fillers — the ones it skipped had the bugs.

---

## Step 8 — RENDER

**→ Use skill: `render`** — the manifest-driven, stage-cached rendering engine.

```bash
# First render
python3 ${CLAUDE_PLUGIN_ROOT}/skills/render/engine.py <project_dir>

# Revision (after editing cuts.json / captions.ass / a value in manifest.json)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/render/engine.py <project_dir> --bump
```

The engine reads `manifest.json`, runs the pipeline's stages in order (`cut → reframe →
grade → captions → mix → leadfix`), caches each stage's output by content-hash to
`10_WORK/stages/<stage>/<hash>.mp4`, then copies the final stage's output to
`20_DELIVER/v<N>/<Brand-named>.mp4`. Pre-existing cache entries are reused; only stages
whose inputs/config changed re-execute.

**What the stages wrap** (the engine handles all of this — do NOT call these scripts
directly once a manifest exists):
- `cut` — frame-accurate cuts from `cuts.json`, multi-source mux (4K master video + lav audio), concat with re-encode.
- `reframe` — `qa_reframe_v2.py` per-segment with the manifest's preset/zoom. Always from 4K master.
- `grade` — locked Speaker/SF color filter (`eq + colorbalance`).
- `captions` — burn `captions.ass` via libass.
- `mix` — voice loudnorm + music + alimiter (music_lufs −30 under voice_lufs −16).
- `leadfix` — head trim ~0.063s + delivery-bitrate re-encode.

**🔒 Reframe = per-segment BEFORE merge, never after.** For assembly cuts, reframe each
segment individually first, then concatenate the reframed segments. Never concat raw segments
then reframe. See `horizontal-to-vertical/SKILL.md` for the full rule (locked 2026-06-11).

**🔒 Captions = always `caption-clips`, never hand-roll.** One engine:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/caption-clips/scripts/spice_caption.py <input.mp4> <output.mp4> [--context "guest asks, Speaker answers"]
```
One preset (`presets/spice.json`, resolution-adaptive). Don't hand-roll a burn or use any old
preset — the `caption_qc` guard will reject it. See `caption-clips/SKILL.md`.

**The standalone skills (`horizontal-to-vertical`, `caption-clips`) are still the single source
of truth for HOW each layer is built** — `render` is the orchestrator that wires them into a
stage chain. Load those skills' SKILL.md when the layer's *behavior* is in question (which
preset, how to write a caption director, etc.). Load `render` when the question is *how to
build/revise the clip*.

**Hard-end rule still applies** — but it's enforced upstream now (cut stage's last segment
ends on the payoff word; no fade-to-black; music has a ~0.6s tail but picture never fades).
Hard-end is a property of `cuts.json`, not a separate stage.

**🔒 `clip.contract.json` is auto-emitted next to the delivered .mp4** (2026-06-12). The
engine writes `20_DELIVER/v<N>/clip.contract.json` containing **declared truth** (from the
manifest: pipeline, reframe preset/zoom/res, caption style + color map, music path + LUFS,
n_segments, first/last labels, speakers) and **observed truth** (from ffprobe on the
rendered file: duration, width/height, codec, fps). Every Step 9 audit agent reads this
FIRST, then verifies pixels/audio match what was declared — any mismatch is a FAIL. Don't
hand-write a contract; let the engine emit it.

**Music sourcing** still per the locked rule: ONLY from `${CLAUDE_PLUGIN_ROOT}/vault/content-skill-system/(1) Tik Tok/`.
Pick per-clip by VIBE, never repeat in a batch, prefer instrumental. The chosen track's
absolute path goes in `manifest.stages.mix.music`.

**Source-file editing recipe (the whole point of the cache):**

| Layer changed | File to edit | Stages re-run |
|---|---|---|
| Caption typo / styling | `10_WORK/captions.ass` | captions + mix + leadfix |
| Cut tweak | `10_WORK/cuts.json` | cut + reframe + grade + captions + mix + leadfix |
| Reframe zoom / preset | `manifest.json` → `stages.reframe` | reframe + grade + captions + mix + leadfix |
| Music swap / level | `manifest.json` → `stages.mix.music` | mix + leadfix |
| Head trim amount | `manifest.json` → `stages.leadfix.head_trim` | leadfix |

**Pipelines available** (`${CLAUDE_PLUGIN_ROOT}/skills/render/pipelines/`):
- `listicle` — numbered rapid-fire, `talking-head` preset, music + leadfix.
- `qa` — single-cam Q&A / hotline, `stage` preset.
- `single` — monologue talking-head (no numbered tabs).
- (multicam podcast / Q&A: TODO — adds an `assembly` stage that wraps `qa_assembly.py`.)

**Bumping a stage's VERSION** — when a stage's code changes meaningfully, bump its `VERSION`
constant in the stage module (`${CLAUDE_PLUGIN_ROOT}/skills/render/stages/<stage>.py`). The cache hash
includes VERSION, so all prior outputs of that stage become stale and re-render automatically.

---

## Step 9 — Optional requested audit

Default runs use source-informed pre-render construction checks and one final file-integrity
check. Do not run independent model audits, six-agent fan-outs or scorecards unless the user
asks for an audit. The active agent can apply the bundled rubric directly to the selected
source words and output; no Anthropic key or separate Claude CLI is needed. A specifically
requested independent model integration remains optional, and missing credentials for it do
not become onboarding requirements for normal editing.

---

## Step 10 — CONFIRM DELIVERY

`render` already wrote `20_DELIVER/v<N>/<Brand-name>.mp4` as part of step 8. Verify it exists,
show the user, log to `_project.md`.

Brand name format: `BRAND_CONTENTTYPE_SOURCE_Title_Editor_YYYYMMDD_V#` — set in `manifest.output.name`.

External review delivery requires an explicit request for the selected batch. Default: deliver locally.
When the user asks to push to Frame.io + Monday → Use skill: `monday`.

---

## ENCODER

Every render: `encoder_args()` from `${CLAUDE_PLUGIN_ROOT}/lib/_shared/fast_encode.py`.
Hardware VideoToolbox (~4× libx264). NEVER hand-write `-c:v libx264`.
Parallel: `_shared/parallel.py run_commands(kind='encode')` (cap 3).

---

## OUTPUT CONTRACTS (every JSON the pipeline produces has a declared schema)

Standard: every JSON file generated by this pipeline (cuts.json, manifest.json,
audit reports, contract.json, eval.json, dream_report.md) declares its schema and is
POST-VALIDATED before any consumer reads it. **No "the model probably got the format
right" — measure it.**

Current schema validators (run them at their named step):

| File | Schema validator | When |
|---|---|---|
| `cuts.json` | `_shared/window_validator.py` + `_shared/handoff_validator.py` | Step 5a + 5b |
| `manifest.json` | `_shared/handoff_validator.py` | Step 5b |
| `clip.contract.json` | emitted by `render/engine.py` (declared + observed side-by-side) | Step 8 |
| audit JSONs (`audit_visual.json`, etc.) | each audit skill returns `{verdict: PASS|FAIL, findings: [...]}` | Step 9 |
| `_dream_report.md` | (markdown — no validator, format is fixed by `memory-curator/scripts/curate.py`) | weekly |

**Rule for new JSON-emitting steps:** before adding a new LLM-generated JSON output to
the pipeline, write its schema validator FIRST and run it on every generation. A
half-truncated JSON that has to be hand-fixed is a 100% indicator the step needed a
validator from day one. (Pattern from Anthropic's prompting playbook — define the
closing structure as part of the prompt + post-validate so malformed outputs fail
loudly instead of corrupting a downstream stage silently.)

---

## REFERENCES (cutting knowledge, all in one place)

| Doc | What it covers |
|---|---|
| `${CLAUDE_PLUGIN_ROOT}/vault/CLIP_CUTTING_PLAYBOOK.md` | THE global cutting bible — seam rules, true-end, fades, dead-air, verification |
| `references/editorial_sop.md` | Speaker Q&A SF editing SOP |
| `references/assembly_cut_standard.md` | Q&A assembly-cut visual standard (locked) |
| `references/visual_guide.md` | Angles, grade, captions, title cards |
| `QA_HOTLINE_SOP.md` | Canonical Q&A/Hotline selection + editing rubric |
| `references/qa_sop_locked.md` | Q&A SOP (locked baseline) |
| `references/qa_format_spec.md` | Q&A format specification |
| `references/qa_v1_baseline.md` | V1 baseline reference |
| `references/v14_baseline_locked.md` | V14 baseline (locked) |
| `references/subtitle_rules.md` | Subtitle placement + rules |
| `references/ui_safe_zone.md` | UI safe zones for captions/overlays |
| `references/hooks_taxonomy.md` | Hook types (contrarian/story/stat/confession/stakes/question/list/reveal) |
| `references/segment_select_tighten.md` | Julian IP: segment → select → tighten flow |
| `references/audio_music_presets.md` | Audio/music Premiere presets |
| `references/ffmpeg.md` | FFmpeg recipes |

---

## SCRIPTS

| Script | What it does |
|---|---|
| `scripts/tam_select.py` | THE clip selection gate (TAM filter) — outputs hook_type + emotion + topic per candidate |
| `scripts/set_caption_context.py` | Wire tam candidate's hook_type into manifest.json → stages.captions.context |
| `scripts/tam_pipeline.py` | Segment → select per exchange (long sessions) |
| `scripts/tam_segment.py` | Split session into per-guest exchanges |
| `scripts/tam_tighten.py` | Within-clip editorial trim |
| `scripts/thread_mine.py` | Cross-timeline narrative thread finder |
| `scripts/transcribe_groq.py` | Per-clip Groq re-transcription (requires boundaries.json) |
| `scripts/transcribe_isolated.py` | L/R channel isolation for call-in audio |
| `scripts/cut_clip.py` | Single clip cut (frame-accurate) |
| `scripts/batch_cut.py` | Batch clip cutting (parallel) |
| `scripts/detect_fillers.py` | Acoustic/regex filler detection |
| `caption-clips/scripts/jumpcut.py` | Dead-air removal (lives in caption-clips, called by edit) |
| `scripts/qa_assembly.py` | Q&A multicam assembly pipeline (single-pass reframe, `--corrections`, `--music-ss`, `--music-delay`) |
| `scripts/qa_prebuild_audit.py` | **REQUIRED Q&A gate (2026-06-16):** 6 pre-build gates run on EDL BEFORE encode — catches the defect classes Operator reviewed Guest for (boundary-tail / guest-completion / payoff-extension / intro=biz+problem / music-intro / guest-eye). Exit 1 = redesign EDL, don't encode. |
| `scripts/qa_build.py` | Q&A render (per-segment cut + reframe + caption) |
| `scripts/qa_audit.py` | Q&A audit gate |
| `scripts/qa_overlap_check.py` | EDL disjoint-segments check |
| `scripts/cut_by_phrase.py` | Podcast phrase-level cutting |
| `scripts/multifinish.py` | Podcast multi-speaker finish |
| `scripts/multicut.py` | Podcast multicam cut |
| `scripts/diarize.py` | Speaker diarization |
| `scripts/sync_audio.py` | Audio sync (cross-correlation) |
| `scripts/make_proxy.py` | 1080p proxy from 4K |
| `scripts/vibe_music.py` | Vibe-matched music selection |

---

> Generic skill — no brand baked in. Per-brand vocab / music / presets / overrides live in that
> brand's folder and are passed by path, never hardcoded here.
