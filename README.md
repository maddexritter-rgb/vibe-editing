# Vibe Editing — starter kit

**Raw footage in → finished, captioned, self-audited vertical clips out, with one command.**

The full short-form pipeline as a Claude Code plugin: ingest → transcribe → mine the best
moments (with a real editorial rubric) → hand-cut → face-track to 9:16 → caption → mix music →
render → construction/file checks → deliver.

> **Start here:** open **`ONBOARDING.md`** and paste it into the **Claude Code** app — it installs
> everything for you and interviews you about your brand (logo, fonts, colors, style), then makes a
> test clip. No editing software, no terminal. `Vibe-Editing-Playbook.pdf` is the deeper walkthrough.

## Quickstart
```
./setup.sh                         # installs ffmpeg + deps, runs a health check
# then ask your agent: "Set up Vibe Editing for me" (ONBOARDING.md)
/edit <your youtube link>          # in Claude Code   — or:   ./bin/vibe-editing "<link>"
```
Check readiness any time: `python3 plugins/vibe-editing/doctor.py`

## What's INCLUDED — the full method
The complete editorial brain ships here (anonymized): the clip-selection rubric, the cut logic,
the spice caption styling, the audit rules, and the lift patterns. **Out of the box it cuts with
real editorial judgment, not a generic template** — the clips come out to a high bar, and the
active agent applies the editorial references and inspects the result; additional audits are opt-in.

## What YOU supply
| You bring | Where |
|---|---|
| Editing connections (AssemblyAI, Gemini, agent reads bundled editorial docs; optional audit/fallback keys) | Agent-guided [onboarding](ONBOARDING.md); secrets saved privately |
| Footage | the project's `00_SOURCE/` (or a URL / drive link) |
| Music | your royalty-free tracks → `VIBE_MUSIC=/your/music` |
| Font & brand | swap the bundled free font; set your cover logo / product name |

## What's deliberately NOT in here
Only the things that are pure liability and add nothing to clip quality:
- **API keys** — bring your own; [complete inventory and setup](docs/CONNECTIONS.md).
- **Licensed fonts** — free **Montserrat** ships instead (looks the same, legal to redistribute).
- **Personal / client data** — real names, revenue figures, and client footage were anonymized
  or removed. None of it is used to make a clip.

## First use and connections
The agent walks through [ONBOARDING.md](ONBOARDING.md) before the first production job and saves
progress. **AssemblyAI** is the primary transcription provider, matching the current `/edit`
pipeline. The active agent reads the bundled editorial documents without an Anthropic/OpenAI key.
Gemini and optional audit/fallback credentials have distinct roles in the
[connection inventory](docs/CONNECTIONS.md). Some current team-native integrations are companion
installations; the inventory names those prerequisites rather than treating keys as a full install.

## Deliver to Frame.io + Monday
Say **“push these clips to Frame and Monday.”** First use connects your own accounts and asks
which brands you edit for, your editor initials, and where each brand's clips should go.
Shorts are the default. The kit handles filenames, Frame uploads and linked Monday review cards.
[Delivery setup](plugins/vibe-editing/skills/monday/references/onboarding.md).
