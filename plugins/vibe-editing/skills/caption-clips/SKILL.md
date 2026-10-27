---
name: caption-clips
description: THE single caption skill. There is ONE caption style for ALL footage — "spice" (the locked the reference editor look). Burns it onto any short-form clip via ONE engine. Default and only style. Use whenever the user says "caption this", "add captions", "pro/speaker/spice/montserrat captions", "caption these clips", "split this reel and caption each one", or "/glow" (glow is retired → produces spice). Trigger keywords caption this clip, add captions, pro captions, spice captions, speaker captions, montserrat captions, caption these, reel captions, subtitle, burnt-in captions, word-by-word captions, glow.
---

# caption-clips — ONE caption style for everything

## Active-agent styling (default)

The current model reads the caption references and the normalized words, then authors the style
stream itself. No Anthropic/OpenAI key or separate Claude CLI is required. Before rendering:
run `spice_format.py --words <pinned-words.json> <spice_norm.json>`, read that indexed word list,
and write `director_stream.json` as `{"words_sha256":"<SHA-256 of spice_norm.json>","words":{"0":{"w":"strong","c":"speaker"}},"voice_spans":[]}`.
Use the preset's weight/size names and source-supported per-word voice colors (`c:"guest"` for a
different speaker). Compute words_sha256 from the exact normalized JSON file. Indices refer to those normalized words
after token merges; changed ASR words invalidate the stream rather than shifting styles silently. Apply voice colors
per word; keep voice_spans empty. Pass `--words <pinned-words.json> --director <director_stream.json>`
to spice_caption.py, or set `stages.captions.words` and `stages.captions.director` in the manifest.
Q&A/multicam wrappers (`qa_assembly.py`, `qa_build.py`, `multifinish.py`) and listicle builds also
accept `--director`; author it against the normalized words each wrapper writes. Historical
scripted model directors/selectors are optional legacy utilities, not the default.


## 🔒 LOCKED CAPTION LESSONS (2026-06-12) — every one shipped a wrong burnt caption; do not regress

> Caption-text errors are INVISIBLE to audio QC (the audio is fine; the wrong word is only on screen).
> They must be caught by scanning the `.ass`/`subs.ass` text or OCR'ing frames — never assumed clean.

1. **"one" defaults to the WORD, never the digit "1".** In conversational speech "one" is almost always
   the pronoun/article ("no one", "one of the most", "the one thing", "is one of"). `spice_format.py`
   numeralizes to "1" ONLY via positive signals: money, an adjacent number, or listicle "number/step/day
   one" → "#1" (handled downstream on the WORD form, so keeping the word never breaks "#1"). Defaulting to
   "1" shipped "no 1 will care" AND "judgment is 1" in one batch. If you touch number rules, re-run the
   probe set (pronoun forms: "no one", "the one", "one of"; listicle: "number one", "step one"; money:
   "one hundred million").
2. **ASR hallucinates rhetorical tags ("right?") that were never spoken — and the chain BURNS them.**
   Whisper pattern-completes a tag from the clip's parallel structure ("take the risk, right? … shake off
   the losses…" → invents a second "right?"). It appears in BOTH the QC re-transcript and the caption
   transcription. To DISPROVE: isolate the exact source region (`ffmpeg -ss X -t 0.5 -af volume=12dB`) and
   transcribe it ALONE — context-free boosted audio can't be pattern-completed. To FIX: pin a hand-corrected
   word list and pass `--words` (skips per-render ASR → deterministic captions across re-renders too).
3. **`--corrections {"heard":"burned"}`** for reviewer-flagged single-word mis-hears (e.g. "quadruple"→
   "quadrupled") without re-cutting. Applied to the transcription before formatting.
4. **The render `captions` stage content-hashes these scripts** (spice_format / spice_caption /
   generate_spice / caption_director) into its cache VERSION (render/stages/captions.py). So editing ANY of
   them auto-invalidates every caption cache — a script fix re-renders instead of silently serving a stale
   burn (which it used to: the "no 1"→"no one" fix appeared to do nothing until this was added).

---


There is **exactly one caption style: spice** (the locked the reference editor look). Same fonts, shadow, weights,
sizes, italics for EVERY clip and footage type. The only things that change are **automatic
decision-tree branches** inside that one style (below). All older styles (`pro`, `pro_locked`,
`speaker_canon`, `ugc`, `glow`) and their generators (`generate_ass.py`, `batch_pro.py`) were **deleted
2026-06-11** — do not look for them. `caption-clips` is the SINGLE source of truth; the `edit`
orchestrator, the `render` skill, the caption-app, `/glow`, `transcript-edit`, listicles, and multicam
all route through this one engine. Never duplicate its rules; never hand-roll a caption burn.

## The ONE command — caption any clip

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/caption-clips/scripts/spice_caption.py <input.mp4> <output.mp4> [--context "<hint>"]
```

This is video-in → spice-captioned-video-out. It runs the locked chain:
`transcribe (AssemblyAI primary; Groq fallback) → spice_format → active-agent style stream → generate_spice --burn → caption_qc`.
The output has the two-layer gblur Premiere shadow, resolution-adaptive scaling, and per-word color.

**Text normalization = `spice_format.py` (The reference editor's company.com spec, locked 2026-06-11).** This single
deterministic engine (154/154 tests) applies all 9 of the reference editor's caption-text rules — lowercase-except
(I-forms/proper/acronyms), number-words→digits + compounds, "one" vs "1" trigger logic, listicle/sequence
labels (#N / day N), money formatting (threshold table + grand + rate phrases + counting-noun/year guards +
triggers), $/%/X symbols, thousands separators, punctuation cleanup, and the locked dictionary — and it
preserves per-word timestamps through token merges (`fifty thousand dollars`→one `$50K` token spanning its
source words). It REPLACED the old `normalize_simple` + `spice_normalize` two-step. Full rules:
`references/spice_caption_formatting_spec.md`. Standalone: `spice_format.py --words <in.json> <out.json>`
(word-mode) or pipe text on stdin.

- `--context` (optional): a director hint, e.g. for a Q&A say *"guest asks the question, Speaker answers"* so
  the guest's lines render yellow. Omit it for a monologue (all white).
- Requires `GROQ_API_KEY` (transcription). `ANTHROPIC_API_KEY` drives the director's color/emphasis; without
  it the chain still produces spice, just with deterministic (no LLM-chosen) color.

If you ALREADY have a spice transcript + style stream (e.g. inside `/edit`'s render or `transcript-edit`),
call the renderer directly with the one preset:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/caption-clips/scripts/generate_spice.py <spice_words.json> \
    --preset ${CLAUDE_PLUGIN_ROOT}/skills/caption-clips/presets/spice.json \
    --style <director.json> --out subs.ass --burn <clip.mp4> --burn-out <final.mp4>
```

**ONE preset: `presets/spice.json`.** It is resolution-adaptive — `generate_spice --burn` probes the real
frame and rescales font/shadow/animation, so the same preset renders correctly at 1080, 4K, anything.
There is no `spice_1080` (deleted) — never branch on resolution yourself.

**NEVER** do a plain ffmpeg `subtitles=` burn of a static `.ass` (no gblur → the wrong "horrendous" look),
and never reach for a deleted preset/generator. The `caption_qc.py` guard (run automatically by
`spice_caption`) **fails the render** if the output isn't the spice gblur path — if you hit a `caption_qc`
error, you used a wrong/deleted path; switch to `spice_caption.py`.

## The only variation: automatic branches WITHIN spice

The style is identical everywhere; these are decided automatically per clip:

| Branch | Behavior |
|---|---|
| **2 speakers** (guest asks / Speaker answers) | guest = **yellow + italic**, Speaker/host = **white**. Driven by the director (`--context` hint) or, in the app, the local diarizer. Monologue → all white. |
| **Split-screen** vs **single cam** | caption sits in the **seam** on split-screen; **below the chin (~50%)** on a single cam. Driven by the layout track when present. |
| **Resolution** | auto-scaled (resolution-adaptive). |

Fonts, the two-layer gblur shadow, the weight ladder, size tiers, italics, and the drop-in animation
are **the same for all of the above**.

## The locked spice rules (baked into the chain — verify on every render)

**Text SOP** (auto via `normalize_simple` → `spice_normalize` → the `generate_spice` chunker):
1. Lowercase everything EXCEPT proper nouns and I-forms (`I`, `I'm`, `I'd`, `I've`, `I'll`).
2. Single line, **≤18 displayed chars AND ≤3 words** per cue; never merge across a sentence boundary.
3. Money keeps `$` and uses symbols/abbreviations: `$2.5K`, `$600K`, `$46.2M`, `$1M`, `%` not "percent",
   `≥$100K` abbreviated. Numbers/money are **white** (emphasis via weight/size) — never yellow.

**The 5 styling axes** (the per-word "director" logic — what makes it the reference editor):
1. **Color = whose voice.** White = Speaker. **Yellow `#FECB00`** = a different voice (Q&A guest, or Speaker
   role-playing/quoting someone — a reported-speech span is **all-or-nothing**: every word first→last is
   yellow+italic+quoted, including function words).
2. **Weight = vocal stress.** Montserrat ladder: Medium (base) → Bold → Extrabold → Black. Base weight is
   **Medium**, never Regular/Light.
3. **Italic** = quoted/role-played/reflective speech — a gentle forward `\fax` shear, not synthetic `\i1`.
4. **Quotes** wrap explicitly role-played speech (always italic; color follows the voice).
5. **Size = intensity**, subtle (10–15%: emph 110 / strong 115 / peak 120). **Per CUE only** — a size bump
   lands on a single-word caption OR the whole line, **never one word inside a multi-word line**. A safe-zone
   cap keeps any bumped line ≤82% frame width. Stacks with color/weight/italic.

**Shadow (LOCKED 2026-06-11):** two-layer gblur Premiere drop shadow, tightened — sigma 48/20, border 13/6,
intensity 0.70/1.0, down-right offset, all font-relative (`spice.json` shadow block). Only `generate_spice
--burn` composites it; a plain `subtitles=` burn cannot.

**Animation:** subtle Premiere "Text Down Small" drop-in — font-relative (8px @1080 / 16px @4K) + a quick fade.

## Listicles — category tabs (the one extra overlay)

For a numbered listicle, after `generate_spice.py` (before the burn) add the persistent "N. CATEGORY" pill:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/caption-clips/scripts/spice_tabs.py <captions.ass> --clip-end <dur> --style glass --y 1140 \
    --point "3.38:#1:OFFER" --point "8.02:#2:MARKETING" ...   # item_start:#N:CATEGORY (hook gets no pill)
# then re-burn. The listicle-short skill (build_short.py) wires this automatically.
```

## Ship gates (run before handoff)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/sf-audit/scripts/audit.py --clip <captioned.mp4> --subtitles <captions.ass> --client <slug>
python3 ${CLAUDE_PLUGIN_ROOT}/skills/scorecard-audit/scripts/scorecard.py --transcript <transcript.json> --clip <captioned.mp4> --client <slug> --exit-on-fail
```

Plus the always-on `caption_qc.py` guard (structural: confirms the spice gblur path) and `caption_lint.py`
(text rules on the `.ass`).

## Files

| Need | File |
|------|------|
| Caption a clip (THE entry point) | `scripts/spice_caption.py` |
| The renderer | `scripts/generate_spice.py` |
| The director (per-word style stream) | `scripts/caption_director.py` |
| The one preset | `presets/spice.json` |
| Guard: is-this-the-spice-render | `scripts/caption_qc.py` |
| Lint: text rules on the .ass | `scripts/caption_lint.py` |
| Listicle category pills | `scripts/spice_tabs.py` |
| Full style spec / evidence | `references/spice_caption_spec.md`, `references/caption_style_corpus.md`, `references/spice_qa_locked_recipe.md` |
| Fonts (full Montserrat family) | `fonts/free_font/` (Adobe-licensed; never web-download) |
