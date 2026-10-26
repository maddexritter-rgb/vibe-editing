---
name: highlight
description: THE highlights-channel clipper, run three ways. MINE mode (given a link or file of a raw Q&A / hotline / podcast recording) mines it into RANKED, postable HORIZONTAL 16:9 "mid" videos optimized for SUBSCRIBER growth (subs_per_1k_views, NOT views/CTR), cuts them, optionally appends YOUR own CTA outro, and delivers them locally to upload as YouTube drafts. POST mode (after you have uploaded those mids as drafts and say "post & schedule these") titles them with the subs-optimized title rules and schedules them to YOUR channel via your own YouTube sign-in. FILL mode (Mode C) auto-fills your social queue with finished 9:16 SHORTS from a folder, posted to YOUR channel via the Buffer API + your own storage (MINE/POST make 16:9 mids; /edit makes the 9:16 shorts that FILL distributes). Trigger keywords highlight, /highlight, mine mids, make mids, highlights channel, Q&A mid, hotline mid, raw Q&A to clips, mine this hotline, post and schedule these, schedule these out, go post these, title and schedule the drafts, fill my queue, fill the buffer queue, auto-post shorts, queue shorts to buffer, schedule shorts.
---

# /highlight — highlights-channel clipper

Turn a long recording (Q&A / hotline / podcast) into ranked, postable **HORIZONTAL 16:9
"mid" videos** for a highlights / clips channel whose goal is **subscriber growth**. Three
modes: **MINE** (recording → finished mids), **POST** (uploaded drafts → titled + scheduled), and **FILL** (Mode C — auto-queue finished 9:16 shorts to your channel via Buffer).

> **Mids are 16:9 horizontal**, ~3–10 min — regular videos, not 9:16 shorts. (For shorts use
> `/edit`.) This skill self-locates via `${CLAUDE_PLUGIN_ROOT}` (the plugin install dir); every
> script finds the plugin root automatically, so nothing is hardcoded to a home folder.

## 🔒 NON-NEGOTIABLES — verify before EVERY delivery, never silently skip
1. **Optimize selection + titles for subscribers (`subs_per_1k_views`), never raw CTR.** The
   whole point of a highlights channel is to convert the *right* viewer into a subscriber.
2. **16:9 horizontal; the cut ends the instant the payoff lands — nothing trails.**
3. **CTA outro is OPTIONAL and user-supplied.** If you drop your own outro clip at
   `brand/cta/outro.mp4` (repo root), `highlight_cut.py` appends it to every mid. No file →
   it skips gracefully. (`--cta <path>` overrides the location; `--no-cta` forces skip.)

**KPI = subscribers (`subs_per_1k_views`), not views/CTR.** Mids are **16:9, ~3–10 min.**

## Three modes — detect which from what the user says
- **MINE** — they give a **link/file** to a raw recording / say "mine these". → produce
  finished mids, deliver locally. **STOP there** — they upload them as drafts themselves.
- **POST** — they say **"post & schedule these" / "schedule these out"** (the mids are already
  uploaded as YouTube drafts on their channel). → title + schedule them.
- **FILL (Mode C)** — they say **"fill my queue" / "auto-post my shorts"**. → queue finished
  9:16 shorts from a folder to their channel via Buffer (see Mode C below).

The full loop: `MINE → (you upload drafts) → POST → (Studio re-export feeds back to sharpen).`

---

## MODE A — MINE  (recording → finished mids)

```
[1] TRANSCRIBE  word-level + diarized (AssemblyAI primary, labeled fallback evidence)
                → highlight_diarize labels Spk0=Host, Spk1=Guest/Caller (no names)
[2] FORMAT      highlight_miner.py format <transcript> --out <work>/miner_input.txt
[3] MINE        Claude segments miner_input.txt → _segments.json (references/cutting_prompts.md STAGE 1)
[4] SCORE+TITLE highlight_miner.py score --segments _segments.json --out-dir <work>/out  → review.md
[5] REVIEW      present review.md; the user picks which to build (post if score ≥ 55)
[6] CUT (+CTA)  highlight_cut.py --src <rec> --words <words.json> --keep '[[a,b],...]' --out <mid>.mp4
                → precision-cut 16:9, then appends brand/cta/outro.mp4 IF it exists. Emits clip.contract.json.
[7] DELIVER     finished mids → your project's delivery folder
```
**Then STOP and hand the mids over.** The user uploads them to YouTube Studio as drafts. Do
NOT auto-upload (the bridge is optional — see bottom).

Mining judgment lives in: `references/cutting_prompts.md` (how Q&A/hotline gets cut),
`references/selection_rules.md` (the scored fields Claude fills), `references/title_rules.md`
(subs-optimized titles — lead with You / quoted-Q / Helping; keep quote marks on quoted
questions; never optimize for CTR).

### Transcription (this kit's standard)
→ Use skill: `long-form-ingest` for AssemblyAI-first transcript evidence and the source listener.
For clip-local caption words the bundled `caption-clips/scripts/transcribe_lv3.py` now selects
AssemblyAI when connected. Keep `GROQ_API_KEY` for audit/fallback helpers, not primary onboarding.

---

## MODE B — POST  ("post & schedule these")

The mids are **already uploaded as drafts** on the user's channel. Title + schedule them with
the subs-optimized rules, via the **YouTube Data API v3** using the user's OWN Google sign-in:

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/highlight/scripts/highlight_post.py \
    --channel-id YOUR_CHANNEL_ID [--limit N] [--slots 12:00,18:00] [--tz America/Los_Angeles]
```
This:
1. Lists the channel's DRAFT/private uploads (the ones you just uploaded).
2. Titles each one via `title_sop.py` (the subs-optimized rules — needs `ANTHROPIC_API_KEY`,
   or falls back to a deterministic local titler from the transcript).
3. Schedules each to the next open daily slot (default 12:00 & 18:00) as `private` +
   `publishAt`, via the YouTube Data API.

### What the recipient must connect (one-time)
- **A Google Cloud project** with the **YouTube Data API v3** enabled, an **OAuth client
  (Desktop app)** downloaded as `client_secret.json`.
- Put it at `${CLAUDE_PLUGIN_ROOT}/config/youtube_client_secret.json` (or pass
  `--client-secret <path>`). First run opens a browser for **their own** Google sign-in and
  caches a token at `${CLAUDE_PLUGIN_ROOT}/config/youtube_token.json`.
- `pip install google-api-python-client google-auth-oauthlib`.
- Their own `YOUR_CHANNEL_ID` (Studio → Settings → Channel → Advanced, or the `UC…` in the
  channel URL). Nothing is tied to any other account.

> POST acts only on YOUR channel via YOUR auth. It schedules as **private + publishAt** (a
> safe, reversible draft-schedule), and `--dry-run` prints the plan without writing anything.

---

## MODE C — FILL  ("fill my queue" / "auto-post my shorts")

Auto-fill a social queue with finished **9:16 shorts** (the ones `/edit` makes): scan a folder,
filter, and queue each to the user's channel on a schedule via the **Buffer API**. Distribution
only — no cutting. (MINE/POST above handle the 16:9 mids.)

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/highlight/buffer/fill_queue.py discover    # find their channel id
python3 ${CLAUDE_PLUGIN_ROOT}/skills/highlight/buffer/fill_queue.py fill --dry-run
python3 ${CLAUDE_PLUGIN_ROOT}/skills/highlight/buffer/fill_queue.py fill --total 10
```
Scans a folder (`--dir`, env `VIBE_SHORTS_DIR`, else the deliver folder); keeps clips with audio
at 10–180s; never queues the same clip twice (local ledger); caps at `VIBE_BUFFER_DAILY_CAP`
(default 10); `--dry-run` previews.

### What the recipient connects (one-time) — Mode C needs THREE of their own things
1. **Buffer token** — publish.buffer.com/settings/api → `export BUFFER_TOKEN=…`
2. **Their channel id** — `fill_queue.py discover` → `export BUFFER_CHANNEL_ID=…`
3. **Storage for public URLs** (Buffer requires a public media URL) — their own Supabase or any
   S3-style bucket: `export SUPABASE_URL=… SUPABASE_KEY=… SUPABASE_BUCKET=…` (or pass
   `--video-url` for a pre-hosted clip).

Nothing is tied to any other account. Full setup: `buffer/README.md`.

---

## Guardrails (hard)
- Optimize for **subscribers**, not views/CTR. Curiosity/"Why" titles only on an explicit reach play.
- Cut **opens on the guest/caller's problem** (with numbers), **ends the instant the payoff
  lands** — nothing trails.
- 16:9 horizontal. No 9:16 reframe. Keep it brand-safe (no prices-as-promises, no personal PII).
- **No clear payoff = not postable.** POST scheduling is gated (`--dry-run` previews first).

## Files
- `references/`: `research_findings.md` (the method's empirical basis) · `cutting_prompts.md`
  + `segmenter_prompt.ts` + `clipper_hookmeatpayoff.ts` (the segment/clip prompts) ·
  `selection_rules.md` · `title_rules.md` · `feedback_loop.md`
- `config/patterns.json` — title-pattern → subscriber weights (the method; tune on YOUR data).
- `scripts/`: `highlight_miner.py` (format+score) · `highlight_cut.py` (cut + optional CTA) ·
  `highlight_cta.py` (append optional outro) · `highlight_post.py` (POST: title+schedule via
  YouTube API) · `title_sop.py` (the titler) · `highlight_diarize.py` (host/guest labels) ·
  `highlight_asd.py` + `highlight_multicam.py` + `highlight_reframe16.py` (two-cam Q&A
  switching) · `highlight_studio_import.py` (import YOUR Studio CSV to retune weights) ·
  `highlight_source_match.py` (link a mid back to its long-form source) ·
  `highlight_publish_bridge.py` (**OPTIONAL** auto-upload — normally you upload manually).
- `buffer/` — **MODE C (FILL)**: auto-post finished 9:16 shorts to your Buffer queue
  (`fill_queue.py` — bring your own Buffer token + channel + storage). See `buffer/README.md`.

## Reuse, don't duplicate (ONE-skill rule)
For **editorial cut quality**, lean on `/edit`'s Q&A standards rather than re-deriving them.
This skill owns mining + boundary-picking for mids; it POINTS to the shared engines
(`lib/_shared/precision_cut.py`, `fast_encode.py`) and to `/edit` for the cut SOP.

## Tune it on YOUR channel (the feedback loop)
Selection + title weights ship as starter defaults. To make them yours: export your YouTube
Studio table (`Content / Views / Impressions CTR / Subscribers / …` as `Table data.csv`) and
import it to measure your real `subs_per_1k_views`, then re-rank `config/patterns.json`:
```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/highlight/scripts/highlight_studio_import.py \
    --csv ~/Downloads/"Table data.csv" --out <work>/studio.json   # preview; add --write-db to persist
```
That closes the loop: post → measure your subscriber conversion → re-weight → mine better.
