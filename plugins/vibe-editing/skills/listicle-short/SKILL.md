---
name: listicle-short
description: One-shot pipeline that turns a long-form video (YouTube link or local file) into a finished ~60-second NUMBERED rapid-fire "listicle" short in the locked Speaker/SF style — the speaker's punchiest verbatim soundbites, cut + assembled, face-tracked 9:16, SPICE captions (via the `caption-clips` skill) with persistent #N tactic numbers, leveled audio. Built 2026-06-04 from the Creator "13 Years of Business Advice" build to make that exact workflow repeatable. Use when the user drops a YouTube link (or long-form file) and wants a 60s numbered short / "listicle short" / "do the 13-years thing" / "turn this into a one-minute clip". Trigger keywords listicle short, listicle this, numbered short, 60 second listicle, drop a youtube link, one shot short, make a short from this video, rapid fire short, lessons short.
---

# listicle-short — long-form → 60s numbered listicle V1, in one shot

**What it makes:** the speaker's best **verbatim** one-liners, cut from a long-form, assembled into a ~60s rapid-fire list, reframed 9:16, captioned in the **SPICE** style with a **persistent glass category pill** (`1. OFFER`, `2. MARKETING`, …) above each tactic, audio leveled to −6 dB. This is the Creator-13-years format, templatized.

## Defaults (override per clip)
- **Flow = script + V1 together.** Produce the numbered script (for sign-off) AND render a V1 in one pass. `--stop-after-script` for script-only.
- **Framing = auto 9:16** (face-tracked). `--stop-after-assemble` outputs the horizontal cut for the user's own reframe/bg (hero clips); they re-run with `--no-reframe` to caption it after.
- **Music = off.** `--music <track>` to add (calm/cinematic from the Speaker TikTok set, never hardcore — see caption-clips SOP).
- **Length ≈ 60s, ~10–14 tactics.** Content-driven, not capped.

## The run (agent steps)
### 1. Ingest
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/listicle-short/scripts/ingest.py "<youtube-url-or-file>" --out ~/Downloads/<slug>/
```
→ `source.mp4` (1080p), `transcript_ts.txt` (read this), `transcript_words.json` (precise word timestamps), `meta.json`. YouTube uses free json3 captions; local files fall back to Groq lv3.

### 2. Mine + write the script (THIS is the editorial judgment — do it well)
Read `transcript_ts.txt`. For coverage on long transcripts, fan out parallel readers (Agent tool) over thirds, then curate. Pick the **punchiest, self-contained, verbatim** one-liners — **prefer the speaker's OWN section/headline lines** if the source is already enumerated ("number one… number two…"). Rules baked from the 13-years build:
- **Each line must be a clean section/idea opener** that stands alone (no dangling "this/that", no mid-story fragment). The #1 failure is pulling a fragment from the middle of a section — it reads random. Verify lead-in.
- Lead with a **credibility/curiosity hook**, end on a **mic-drop**.
- Keep each ~2–6 s; aim ~10–14 lines for ~60s. Renumber 1..N (your own count).
- **VERIFY every quote against the source** (exact wording + precise in/out from `transcript_words.json`). The sub-agent mining timestamps drift — always confirm against the word stream.

Author the **spec** (`spec.json`) — `in`/`out` are LONG-FORM seconds; omit `"n"` for the hook. Give every
numbered tactic a **`"cat"`**: a SHORT (one word, UPPERCASE) category naming its theme — it renders in the
glass pill as `1. OFFER`. Pick from the tactic's actual content (OFFER · PRICING · SALES · MARKETING · TRUST ·
FOCUS · SCALE · MINDSET · CUSTOMERS · PRODUCT …); repeats are fine. Keep it one word so the pill stays compact.
```json
{ "title": "13-years-of-marketing",
  "segments": [
    {"in": 9.33, "out": 12.86},
    {"in": 12.60, "out": 17.28, "n": 1, "cat": "OFFER"},
    {"in": 17.92, "out": 20.48, "n": 2, "cat": "MARKETING"} ] }
```
Post the script (numbered, **with each tactic's category**, with timestamps) for the user/Speaker to approve.
If `--stop-after-script`, stop here. (If a numbered segment is missing `cat`, the build falls back to plain `#N`.)

### 3. Build the V1 (one command — does everything else)
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/listicle-short/scripts/build_short.py \
    --source ~/Downloads/<slug>/source.mp4 --spec spec.json --out ~/Downloads/<slug>/
    [--no-reframe] [--stop-after-assemble] [--music <track.mp3>] [--director stream.json]
```
It runs: cut every soundbite (frame-accurate) → concat → **face-tracked 9:16** → transcribe → normalize → **spice_normalize** (money/symbol SOP) → **caption director** → `generate_spice` (captions auto-lowered to 66% to clear the pill) → **`spice_tabs --style glass`** (the locked "N. CATEGORY" pill) → **level audio** → `<title>_v1.mp4`.

**Locked tab look (2026-06-05):** dark frosted **glass capsule**, warm-gold number + white category on one line (`1. OFFER`), ink-centered, soft diffuse shadow, sitting just above the (lowered) caption. Persists per tactic. It's the default whenever the spec carries `cat`s — no flags needed. To change the look, `spice_tabs.py --style mono|glass|ivory` (glass is locked). Geometry lives in `caption-clips/scripts/spice_tabs.py`; caption drop + pill height are `LISTICLE_CAP_PCT` / `PILL_Y_RATIO` in `build_short.py`.

**Caption director:** for best quality, author the 5-axis per-word style stream yourself and pass `--director stream.json` (the editorial styling — color=voice, weight=stress, size on solo payoffs only, etc. — see caption-clips SKILL.md "Dynamic spice captions"). The active agent authors this stream against the normalized words; pass it with `--director`. Missing styling returns to the active agent instead of starting a model API or CLI.

### 4. QC before delivery (non-negotiable)
Pull frames (`ffmpeg -ss <t> -i out.mp4 -frames:v 1`) and **look**: face centered, captions synced + legible, the `#N` lands on each tactic and persists, money/SOP correct, peak ≤ −6 dB. Fix and re-render before calling it V1.

## Notes / gotchas (learned)
- **One file, one obvious name.** Don't leave stale "FINAL" variants in Downloads — it caused a "nothing changed" confusion. Overwrite or move old versions to a `_old/` subfolder.
- **Re-timing after a user trim:** if the user trims the hook and sends a shorter cut, don't re-mine — `transcribe` the trimmed clip, match a few anchor words against the original to get the exact offset, shift the existing `.ass` by it (drop/clamp cues before 0), and re-burn. Preserves the approved captions. (See the 13-years session's `shift_captions.py` pattern.)
- **Don't silently re-level a user's exported audio** — flag if hot, offer the limiter.
- **Reframe across a supercut** uses a single median-X track; fine for a locked-camera talking head, can drift if the subject moves a lot between cuts → reframe per-segment for those.
- Everything downstream of the spec is deterministic + reuses the locked caption-clips scripts — so quality only improves as those rules improve.

## 🛑 LONG RAW FOOTAGE → LISTICLE — the hard lessons (2026-06-06, a REPEATED mistake)
These company.com recordings are SCRIPTED listicle videos (Speaker states a HOOK, then numbered points), shot as
raw multi-take sessions. Four non-negotiables, learned the hard way (twice now):

1. **CUT WITH `_shared/precision_cut.py` ONLY — NEVER hand-roll a "fast" cutter.** I built a fast input-seek batch
   cutter to save time; it CLIPS WORD TAILS (no true-acoustic-end detection) and leaves AWKWARD PAUSES between lines
   (no jumpcut pass). Operator: *"very, very not good."* The ONE clip I left on precision_cut (PersuasionHacks) was the
   only one he liked — that's the proof. precision_cut on a **720p proxy** = accurate AND fast. (Bonus failure mode:
   input-seek + an ABSOLUTE `afade=out` silently MUTES deep-seek segments.) This is the SAME mistake as the Money
   Rules 80-round saga. **Stop rebuilding the cut engine by hand.**

2. **The HOOK = the REAL opening framing line Speaker says to OPEN the video** (skip the production chatter + retakes at
   the head). These are scripted — the hook is at the START. Real examples from this batch:
   - *"These are brutally honest truths you should know before it's too late."*
   - *"I became a millionaire when I was 26… in this video I'm going to give you the real-world advice I wish someone gave me earlier."*
   - *"I've had some of the hardest days of my life this year… in this video I want to share a four-part framework…"*
   - *"Here's 14 years of business advice in 60 seconds."*
   A punchy line pulled from the MIDDLE is a LISTICLE POINT, **not** the hook. Mining MUST grab the opening framing line.

3. **Multi-source shoots (CAMA video + a separate `AUDIOLAV/*.wav` lav):** the camera's embedded scratch audio is BAD.
   SYNC the lav to the cam (xcorr / `silencedetect`-anchor against the cam audio for the offset) and use the **LAV** as
   the clip's audio.

4. **Go ONE BY ONE — not a batch.** QC EACH: hook is the real opener · no clipped words · no awkward inter-line pauses ·
   audio is the good source · ~50–60 s. Operator explicitly asked not to batch these.

5. **VERIFY the hook by mapping the first span back to the transcript TEXT — don't trust a remembered phrasing.** Spans
   are just timestamps; print the words each opening span covers before committing a 70-min batch. Speaker often
   SELF-CORRECTS on camera (e.g. starts "brutally honest…" then "*instead of brutally honest… I'll say real-world*") —
   the line you remember may never have been delivered cleanly. Use the clean intro framing that WAS said
   (e.g. *"I wanted to make a video about five business lessons I just learned crossing 250M in 2024"*).

6. **When you add a CLI flag, WIRE IT THROUGH to the actual call — not just the arg parser.** `reframe.sh` parsed
   `--nose-y` into `$NOSEY` but the `reframe_h2v.py` line still hard-coded `--nose-y-1080 719`, so the "bump him up"
   note silently did NOTHING for a whole render + a re-render. **Always A/B two frames (old vs new value) to PROVE a
   framing/visual flag actually changed the output** before batching. `--nose-y 580` ≈ "bump up / less headroom" vs the
   719 default (lower value = subject higher).

7. **A deleted proxy → `precision_cut` returns 0 spans SILENTLY.** If a cleanup swept a clip's `proxy_720.mp4`, span
   capture prints "0 spans" with no error. Rebuild the proxy (the spec still matches) and re-capture before batching.

8. **"He's too low / lost in the frame" is usually a ZOOM (face-SIZE) problem, not just vertical position — MEASURE the
   reference, don't guess `--nose-y`.** Detect Speaker's face in the reference shorts Operator gave you and compute two numbers:
   **face-H** (face bbox height ÷ frame height) and **eye-Y** (eye line ÷ frame height). The locked company look ≈
   **face-H 0.26–0.28, eye-Y 0.285–0.30** (13Years/MoneyRules). Default `--zoom 1.15` renders face-H ≈ 0.19 — way too
   small, reads as "too low." For these single-cam desk listicles use **`--zoom ~1.45` + `--nose-y ~625`** (most sources
   are 4K → stays sharp; only some C-series cams are 1080p). Iterate 2–3 short test reframes, re-measure face-H/eye-Y,
   converge to the reference — THEN batch.

9. **Tracking jitter on a seated talker → lock X static per segment (`--lock-x`), don't just raise `--smooth`.** Per-frame
   nose tracking micro-jitters even at smooth 51. For short per-phrase crops of a near-static talker, `--lock-x`
   (X = segment median, like the Y-lock) is glassy. ⚠️ A huge `--smooth` does the OPPOSITE — `smooth_curve` returns the
   RAW curve when window > frame-count, so it gets *jerkier*. Use `--lock-x`.

10. **`build_short`'s concat is `-c copy` with NO join fades → audible clicks ("cuts are rough").** Add a 10ms/15ms
    de-click a-fade-in/out per segment cut; precision_cut spans already carry silence margins (acoustic-end + clamped
    start) so the ramp lands in silence, not on words. (Wired into build_short's cut step 2026-06-07.)

11. **A single fixed `--zoom` will NOT fit a whole batch — the speaker sits at a different distance in each shoot.**
    Render the batch at a base zoom, then MEASURE each clip's output face-H; for any clip outside ~0.22–0.29, re-render
    that ONE clip with `--zoom = base_zoom × 0.25 / measured_faceH`, and re-measure to confirm. In one 8-clip batch the
    right per-clip zooms ranged **1.1** (a close lav/CAMA shot, face-H 0.327 at 1.45) → **2.2** (a wide shot with a light
    stand in frame, face-H 0.160 at 1.45). Target face-H ≈ 0.24–0.26, eye-Y ≈ 0.285. A high zoom also crops studio gear
    out of a wide shot — bonus.

12. **A separately-recorded lav can mix ~2 dB QUIETER than camera-audio clips through the SAME loudnorm chain.** One
    lav-sourced clip landed −20.6 LUFS while its camera-audio batch-mates hit −18 to −19. Single-pass loudnorm doesn't
    self-correct that. Fix: **2-pass loudnorm the FINISHED clip to match the batch (≈ −18.5)**. ALWAYS LUFS-check a
    lav-sourced clip against its batch-mates before shipping.

13. **1080p source + high zoom → soft; a mild post `unsharp=5:5:0.6` restores it WITHOUT crunching captions/tabs.**
    The lone 1080p source (vs the batch's 4K) measured varLap 70 at zoom 1.45; unsharp 0.6 brought it to 127 (≈ the 4K
    clips' 130) and the white captions + glass pills stayed clean (no halos). Burn it on the finished clip.

14. **Number tabs by LESSON, not by sentence — group related lines under ONE tab.** A lesson can span multiple
    sentences ("Sell to the rich… The middle is where you get killed." = ONE lesson; "weakest link… dumb rules… raise
    the bar." = ONE lesson). To group: give ONLY the first segment of a lesson an `n`+`cat`; continuation segments get
    NO n/cat — build_short emits a tab point only where `n` is set and `spice_tabs` persists each pill until the next
    point, so the lesson's tab stays up across its sentences. Re-check every clip for over-splitting (one "15-point"
    clip was really 12 lessons).

15. **A lav/WAV is often single-sided AND quiet — fix BOTH or it ships broken.** (a) CHANNEL: a mono lav lands on the
    LEFT channel only ("audio only out of my left speaker"; measured L −18.5 / R −26.8). Force dual-mono on the source
    BEFORE the build: `-af "pan=stereo|c0=c0|c1=c0"`, then verify L≈R. (b) LEVEL: the same lav mixed ~2 dB quiet (−20.6
    vs the batch's −18.5) — 2-pass loudnorm the finished clip to match. ALWAYS check L/R balance AND LUFS on a lav clip.

17. **Eye-level consistency — lock every cut to the SAME output eye-line, and MEASURE it per-segment, not per-frame.**
    `build_short --eye-lock` measures each cut's eye-line (Haar box top + 0.4·h, median over ~10 frames) and crops so
    it lands at a fixed output level (`--eye-y-out`, default 566 ≈ 0.295) — so eyes sit dead-level across every cut AND
    across clips (independent of zoom/face-size). ⚠️ VALIDATION TRAP: a single-frame eye-Y sweep shows ~0.08 "spread"
    that is mostly per-FRAME Haar jitter, NOT real drift — measure the per-SEGMENT MEDIAN eye-Y (≈0.027 spread when
    fine) to judge it. Static Y-lock can't follow WITHIN-cut movement (if he leans during a take his eyes drift in a
    fixed frame) — that's his motion, not a framing bug; gentle smoothed Y-tracking is the only fix for that, at the
    cost of the hard lock.
    ⚠️ QC FALSE-POSITIVE: Haar also detects the brand LOGO on his shirt as a "face" (faceH≈0.15, eye-Y≈0.5), which
    inflated one clip's measured eye-spread to 0.48 (real value 0.03). FILTER detections to faceH 0.19–0.40 AND
    eye-Y 0.15–0.42 before computing spread, or you chase a phantom drift. Real per-seg spread ≈0.03 (4K)–0.06 (1080p).

16. **Listicle pacing — tighten the transition INTO each lesson.** Trim each span's trailing silence and drop any
    leading bleed from the cut-out sentence (start exactly on the lesson's first word). Caveat: if that first word is
    itself slow/drawn-out (a soft "Aaalways"), that reads as "emptiness" but it's DELIVERY, not silence — tightening
    the gap helps, but don't time-stretch his word; flag it instead of over-trimming. (And the playbook's fricative
    rule still bites: a word ending in -s like "conversations" needs its span extended ~0.2 s past Whisper's label or
    the tail clips.)

## ⭐ RAW CAMERA FOOTAGE + GRADED BRAND + DELIVERY (Money Rules, 2026-06-06 — the 80-round lessons)
When the source is a RAW multi-take camera session (not a clean YouTube long-form), `build_short`'s
apmontserratte-float cutting FAILS — Money Rules took **80 revision rounds** because of it. Apply seam discipline:

**Cutting (the hard part):**
- Cut with the **`_shared/precision_cut.py` engine + `${CLAUDE_PLUGIN_ROOT}/vault/CLIP_CUTTING_PLAYBOOK.md`** — word-index
  keep-spans, each ENDING at the word's TRUE acoustic end (`silencedetect`, NOT Whisper's ~0.1-0.25s-early label).
  `build_short` still hand-rolls float in/out → **upgrade pending**; until then apply the playbook by hand.
- **PER-SEGMENT reframe** (`reframe.sh` per seg → concat) — a single-median track makes the head JUMP at every cut.
- **ASR LIES**: word.start runs late (catches the previous word — "passive" labeled 0.4s early let "number one"
  bleed in at −14 dB), word.end runs early (clips the tail — "twice/money/once/reality" came out short), and it
  hallucinates leading "So/And/Two/Because" at seams. VERIFY a seam by **level-check** (`volumedetect` the onset:
  loud = real bleed, quiet = the intended word) + spectrogram + the user's EAR — NEVER the transcript.
- **Fused leading words** (spoken "number two", "and so") can't be cut at the next word's onset — route to the next
  PLOSIVE-onset word or a natural PAUSE; drop a small word ("the", "Most") if needed. Cleaner beats complete.
- **Endings RING OUT** — extend past the true end, de-click only (0.02-0.04s), never a tail-fade over the last word.

**Graded brands (Speaker) = GRADE BEFORE CAPTIONS:**
1. Deliver the **uncaptioned 4K cut** (`reframe.sh --res 4k`) for the colorist — NO captions/tabs (the grade cooks them).
2. Colorist returns graded clip → re-transcribe IT (clean cut → clean transcript) → rebuild captions + glass tabs at
   4K (spice.json 4k preset + `y_percent 66`; `spice_tabs` auto-scales 2× from PlayResX; `--y round(H*0.594)`),
   synced to the graded timeline. Usually only "no BS" needs the mishear fix.
3. **Music** from `${CLAUDE_PLUGIN_ROOT}/vault/content-skill-system/(1) Tik Tok/` (Calm or Core, NEVER Phonk) mixed LOW:
   `[1:a]loudnorm=I=-30:TP=-9,afade in 1.5/out 2.5; [0:a][m]amix=normalize=0; then [mix]loudnorm=I=-16:TP=-6`.
   **Cornfield Chase** (Calm) is the safe locked Speaker pick. (TP must be ≥ −9 or loudnorm errors.)

**Delivery (Brand SOP — `engine-file-nomenclature-sop`):** rename to
`SPEAKER_SF_DTC_<TitleCaseHook>_Operator_<FOOTAGE-SHOT-DATE>_V1.mp4` — **SF** (short-form deliverable, NOT LF/SHORT),
**DTC** (talking-head), date = when the SOURCE was shot (camera XML CreationDate), not delivery day. **Then
deliver LOCALLY to `20_DELIVER/` and show Operator — STOP THERE by default.**

🛑 **NEVER push to Frame/Monday on your own.** Only after Operator EXPLICITLY says to push THIS exact file, in THIS
conversation (a prior "upload it" does NOT carry to a V2/re-render/sticker change/next clip — ASK EVERY TIME):
`delivery-workflow/deliver.py <file> --approved` → Team Speaker Social → 02_Social → 01_Short Form →
<MONTH> → 03_EXPORTS. Operator clicks Share in the Frame UI (API can't create shares) → f.io link → Monday `create_item`
(For Review group `group_mm0zk2mx`, board `18399767187`, link col `link_mm0tpgtg`). Monday name = Frame name minus `_V#`.

## 💬 ManyChat CTA overlay (per-video keyword) — locked 2026-06-06
After Operator approves V1 (graded + captioned), most clips get a ManyChat comment-to-DM CTA layered on
as V2. The **locked CTA style** (built off the Money Rules iteration — the gold-badge/icon/pulse versions
were rejected as "too AI / too highlight-y"):

- Two centered lines above the face, **matching the existing captions** (white Montserrat, soft shadow) —
  reads as part of the edit, not a sticker pasted on.
- Line 1 (Medium):   **Want to watch the full video?**
- Line 2 (Medium "Comment " + **Bold quoted** keyword):  Comment 'KEYWORD'
- Both lines **same font size**, tight ~170px gap (4K), centered around y≈560/3840 — clear of the glass
  tabs and captions.
- **Fades in at ~25s** (default) and rides to clip end — hook plays naked.
- **NO badge, NO gold chip, NO comment-bubble icon, NO capsule, NO pulse/animation** — just a clean fade-in.

The keyword is **per-video** (rules / playbook / secrets / scale / closers / etc.) — Operator picks it
per clip to match the ManyChat trigger.

**Build it:**
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/listicle-short/scripts/cta_overlay.py \
    --graded   10_WORK/<TitleHook>_graded_4K.mov \
    --ass      10_WORK/grad_tabs.ass \
    --keyword  rules \
    --audio-from 20_DELIVER/<...>_V1.mp4 \
    --out      20_DELIVER/<...>_V2.mp4
    [--start 25]  [--prompt "Want to watch the full video?"]
```

Key invariants the script enforces (do NOT bypass):
- Appends CTA cues to the existing `.ass` (auto-scales 4K geometry to PlayResY for any res).
- **Audio is copied byte-for-byte from `--audio-from`** (the approved V1) — V2 audio md5 == V1 audio md5.
  Never re-level the approved master.
- Output is the V2 file in `20_DELIVER/`. **LOCAL only.** No upload, no Monday call — those stay manual
  per the rule above.

Operator usually **manually uploads** the V2 in Frame so it version-stacks onto V1 (the V4 API can't
attach to an existing file as a new version) — wait for him to send the new share link, then update
Monday with `delivery-workflow/update_item.py` (link col `link_mm0tpgtg`).
