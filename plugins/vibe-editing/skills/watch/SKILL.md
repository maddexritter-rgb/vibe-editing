---
name: watch
description: >
  Give Claude real eyes + ears on video. Two jobs: (1) WATCH / COMPARE — understand what is
  in a clip, and compare it against a reference reel or a previous version; (2) QA-EYES —
  read burnt-in captions (OCR), check caption placement, framing, pacing and loudness on a
  finished 9:16 short. Wraps the claude-video-vision MCP (Gemini or local large-v3 backend)
  for frame + audio understanding, plus local ffmpeg / tesseract / PIL scripts for contact
  sheets, caption OCR, A/B frame diffs, and an ears + pacing probe. Use when the user says
  "watch this", "what's in this video", "compare these two clips", "does this match the
  reference", "read the captions on this", "look at this clip", "give it an eyes pass", or
  whenever an audit needs to actually SEE burnt-in captions / framing rather than guess.
---

# watch — eyes & ears for video

The thing that makes "watch a video well" work is converting video into things Claude
reasons over natively: **frames as images** (Claude sees them directly) + **an accurate
transcript** + **timing/loudness metadata**. This skill is the disciplined way to do that,
tuned for short-form editing QA and reference-matching.

## How Claude actually sees video

1. **Frames → my own eyes.** The MCP `video_watch` with `frame_mode:"images"` (now the
   default — see config below) returns real JPEGs into the conversation. I look at them
   directly; there is no lossy "describer" model in between. The local scripts here do the
   same via ffmpeg → PNG that I then Read.
2. **Audio → transcript.** The MCP transcribes with the configured backend. For *editing*
   captions, keep using the canonical `long-form-ingest` stack (AssemblyAI primary with provider evidence)
   — it is better on proper nouns. Use the MCP transcript for *understanding/QA*, not for
   generating final captions.
3. **Long-form (>~3 min) → Gemini.** When the Gemini key is set, flip the backend to
   `gemini-api` and Gemini ingests the whole video in one pass (timestamps, what happens,
   where the energy is). Without it, dense local frames + transcript still work, just at
   higher token cost — so segment first.

## Decision tree — which tool, when

| Goal | Use |
|---|---|
| Understand a whole long-form fast | MCP `video_analyze` (structure) → `video_watch` Gemini one-pass |
| Eyeball a finished short's arc | `contact_sheet.py` → Read the PNG |
| Check burnt-in caption placement/presence | `caption_ocr.py` (placement) + contact sheet & your eyes (exact text) |
| Compare my render vs a reference reel | `ab_diff.py --mode side` |
| See exactly what changed v1 → v2 | `ab_diff.py --mode diff` |
| Check audio levels / pacing / black frames | `probe.py` (or MCP `video_analyze` loudness/silence) |
| Zoom into one tense moment frame-by-frame | MCP `video_detail` with a tight segment + high fps |

**Default rhythm for a clip I have not seen:** `contact_sheet.py` first (one image, whole
arc) → decide which moments matter → `video_detail`/`video_watch` on just those segments at
higher fps. Never blast a 13-min file at high fps; analyze, then drill.

## MODE A — WATCH (understand a video)

```bash
# 1. structure first (no frames pulled) — find the segments worth looking at
#    MCP: video_analyze {scene_changes, silence, loudness, motion, black_intervals, transcription}
# 2. then look at the interesting segments as images
#    MCP: video_watch path=<file> segments=[{start,end,fps}] frame_mode=images
```
For a quick local overview without the MCP: `contact_sheet.py FILE --n 16 --cols 4`.

## MODE B — COMPARE

```bash
# my render vs a reference reel (style, caption look, framing, pacing)
python3 scripts/ab_diff.py MINE.mp4 REFERENCE.mp4 --mode side --n 6 \
    --label-a mine --label-b ref --out ~/Downloads/compare_side.png

# v1 vs v2 of the SAME clip — red highlights exactly what moved/changed
python3 scripts/ab_diff.py clip_v1.mp4 clip_v2.mp4 --mode diff --n 9 \
    --out ~/Downloads/compare_diff.png
```
Then Read the PNG and report differences concretely (timestamp + what changed).

## MODE C — QA-EYES (finished 9:16 short)

```bash
# caption presence + vertical placement (reliable); OCR text is advisory
python3 scripts/caption_ocr.py final.mp4 --interval 0.5 --out ~/Downloads/audits/final_captions.json
# for EXACT caption text / spelling, read the contact sheet below with your own eyes

# ears + pacing against the Team Speaker gate
python3 scripts/probe.py final.mp4 --out ~/Downloads/audits/final_probe.json

# visual confirmation of framing / safe-zones / caption look
python3 scripts/contact_sheet.py final.mp4 --n 12 --cols 4
```
Caption-placement target per SOP: text y-center ~65-80% of height (below the chin).
OCR is **advisory** — heavy/stylized fonts can misread; the contact sheet is the final eye.

## Scripts

- `contact_sheet.py INPUT [--n --cols --start --end --tile-w --out]` → one overview PNG.
- `caption_ocr.py INPUT [--interval --band-lo --no-ocr --out]` → caption presence + y-placement (reliable) + advisory OCR text.
- `ab_diff.py A B [--mode side|diff --n --thresh --label-a --label-b --out]` → comparison PNG.
- `probe.py INPUT [--out]` → loudness/peak/silence/black/scene-cut JSON + gate notes.

All are self-contained (ffmpeg + tesseract + PIL/numpy); no MCP required.

## Backend config

Persisted at `~/.claude-video-vision/config.json` via the MCP `video_configure` tool.
Current: `frame_mode=images`, `frame_resolution=1024`, `whisper_model=large-v3`,
`enable_index=true`, `audio_model=gemini-3-flash-preview`.

- **To turn Gemini on** (native long-form watching + best audio): set `GEMINI_API_KEY`
  through the host's private environment (never hardcode a secret in `.mcp.json`), then
  `video_configure backend=gemini-api` if that MCP add-on is installed. See `docs/CONNECTIONS.md`.
- **Privacy:** the `gemini-api` backend uploads footage to Google. For client work that
  must stay local, keep `backend=local` (large-v3 + local frames) — everything in MODE A/B/C
  except Gemini one-pass watching still works fully offline.

## Integration — sf-audit gets eyes

`sf-audit` computes metrics but never *looks*; its subtitle checks degrade to
"flag-for-manual" when captions are burnt-in. The audit's **Eyes pass** (added to that
skill) runs `caption_ocr.py` (covers spelling #1, lower-half placement #3, safezone #7 for
burnt-in text) + `contact_sheet.py` (so the auditor visually confirms framing #13/#14) +
`probe.py` (audio #8-#11, black frames #15). Run it whenever there is no `.ass` sidecar.

## Gotchas

- Don't return hundreds of base64 frames into context — segment, then sample. `view_sample`
  / `--n` keep token cost sane.
- `large-v3` (no turbo) is accurate but slow on long files; that is fine for QA, not for
  bulk transcription — use the canonical `long-form-ingest` stack for that.
- OCR reads what is on screen, including watermarks/UI; trust the caption-band crop (`--band`)
  and your own eyes over raw token lists.
