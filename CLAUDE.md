# Vibe Editing — how to run this project

This folder is **Vibe Editing**: a pipeline that turns a long video into finished, captioned,
face-tracked **vertical clips**, in the creator's own brand. It's built to be run by
**non-technical creators** — so when you help someone here, do the work yourself, never make them
run terminal commands or hand-edit files, and explain what's happening in plain English.

## First production use: onboarding
Read `ONBOARDING.md` and run `python3 plugins/vibe-editing/onboard.py status` before the first
setup/editing/delivery job. Resume the saved interview and connect the current `/edit` providers
from `docs/CONNECTIONS.md`; **AssemblyAI is primary transcription**. Keep team-native bridge or
editorial-document sources labeled accurately. No Anthropic/OpenAI key or separate Claude CLI is
required: the active model reads those documents and makes editorial/style decisions. Ask one question at a time and save secrets
privately. This requirement does not apply to read-only questions or repo development.

## Making clips (the main job)
When the user gives you a video — a YouTube/URL link or a local file — and asks for clips in ANY
plain-English way ("make clips from this", "cut this up", "shorts from this", "/edit <link>"):

1. Read **`plugins/vibe-editing/skills/edit/SKILL.md`** and follow its spine end-to-end.
2. Put source footage in the project's `00_SOURCE/`, scratch in `10_WORK/`, and finished clips in
   **`20_DELIVER/`**. Show the user the delivered clips when you're done.
3. The pipeline mines the strongest moments, hand-cuts, face-tracks to 9:16, captions, mixes music,
   renders, and checks construction/file integrity. Extra audits run only when requested.

You do **not** need the `/edit` slash command or any plugin install — run the workflow directly
from the scripts in `plugins/vibe-editing/`. (A `/edit` shortcut is available if the user wants it:
`/plugin marketplace add .` then `/plugin install vibe-editing@vibe-editing-marketplace`.)

## Horizontal "mid" videos (the highlight skill)
If the user wants HORIZONTAL 16:9 "mid" videos for SUBSCRIBER growth from a long recording —
"mine highlights", "make mids", "highlights channel", "post and schedule these" — read
`plugins/vibe-editing/skills/highlight/SKILL.md` and follow it. These are regular 16:9 videos,
NOT 9:16 shorts (shorts = the edit pipeline above). The CTA outro is user-supplied and optional
at `brand/cta/outro.mp4`. POST mode titles + schedules to the user's OWN YouTube via their own
Google sign-in — never any other account.

## Frame.io + Monday delivery
When the user says "push these clips to Frame and Monday", "send these for review", or `/monday`,
→ Use skill: `plugins/vibe-editing/skills/monday/SKILL.md`.
Its first-use interview connects their own accounts and saves per-brand routes; shorts are the default.

## Installation and credentials
`ONBOARDING.md` owns the first-run interview; `INSTALL.md` owns local installation;
`docs/CONNECTIONS.md` owns the credential inventory. Keys auto-load privately from
`~/.config/vibe-editing/keys.env`. Never ask for API keys in chat or write real keys to the repo.

## Brand it / change it
Brand assets live in `brand/` (logos, fonts, music, caption-style, animations). When the user wants
their brand applied, or any change ("captions bigger", "use this logo", "cut tighter", "don't open
on a question"), update the right config and re-run:
- captions → `plugins/vibe-editing/skills/caption-clips/presets/spice.json`
- font → bundled `plugins/vibe-editing/skills/caption-clips/fonts/` (or their own in `brand/fonts/`)
- what makes a clip worth cutting → `plugins/vibe-editing/skills/edit/prompts/clip_select.md`
- music → `brand/music/`

## Rules
- Only use this kit — don't pull tools or keys from anywhere else on the machine.
- Never delete the user's source footage. Re-renders overwrite the delivered clip in place.
- Be patient and plain-spoken; assume they've never used a terminal.
