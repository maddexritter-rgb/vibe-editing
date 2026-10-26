# Vibe Editing — starter kit

**Raw footage in → finished, captioned, self-audited vertical clips out, with one command.**

The full short-form pipeline as a Claude Code plugin: ingest → transcribe → mine the best
moments (with a real editorial rubric) → hand-cut → face-track to 9:16 → caption → mix music →
render → 6-gate audit → deliver.

> **Start here:** open **`ONBOARDING.md`** and paste it into the **Claude Code** app — it installs
> everything for you and interviews you about your brand (logo, fonts, colors, style), then makes a
> test clip. No editing software, no terminal. `Vibe-Editing-Playbook.pdf` is the deeper walkthrough.

## Quickstart
```
./setup.sh                         # installs ffmpeg + deps, runs a health check
# (optional) paste a free Groq key in plugins/vibe-editing/config/keys.env
/edit <your youtube link>          # in Claude Code   — or:   ./bin/vibe-editing "<link>"
```
Check readiness any time: `python3 plugins/vibe-editing/doctor.py`

## What's INCLUDED — the full method
The complete editorial brain ships here (anonymized): the clip-selection rubric, the cut logic,
the spice caption styling, the audit rules, and the lift patterns. **Out of the box it cuts with
real editorial judgment, not a generic template** — the clips come out to a high bar, and the
6-gate audit rejects anything weak.

## What YOU supply
| You bring | Where |
|---|---|
| API keys (Groq for transcription; Anthropic for best captions) | `plugins/vibe-editing/config/keys.env` |
| Footage | the project's `00_SOURCE/` (or a URL / drive link) |
| Music | your royalty-free tracks → `VIBE_MUSIC=/your/music` |
| Font & brand | swap the bundled free font; set your cover logo / product name |

## What's deliberately NOT in here
Only the things that are pure liability and add nothing to clip quality:
- **API keys** — bring your own (free Groq tier, or `pip install faster-whisper` for local).
- **Licensed fonts** — free **Montserrat** ships instead (looks the same, legal to redistribute).
- **Personal / client data** — real names, revenue figures, and client footage were anonymized
  or removed. None of it is used to make a clip.

## Note on captions
The signature captions transcribe the clip first — so they need a **Groq key** (free tier) or
local whisper. Reframing, cutting, and rendering work without any key.

## Deliver to Frame.io + Monday
Say **“push these clips to Frame and Monday.”** First use connects your own accounts and asks
which brands you edit for, your editor initials, and where each brand's clips should go.
Shorts are the default. The kit handles filenames, Frame uploads and linked Monday review cards.
[Delivery setup](plugins/vibe-editing/skills/monday/references/onboarding.md).
