# Start here — Vibe Editing

Open this repo in your agent and say:

> **Set up Vibe Editing for me. Follow ONBOARDING.md, walk me through getting my own keys,
> ask one question at a time, and save my progress so we can resume.**

The agent handles installation and configuration. You handle account sign-ins and entering
secrets in a private prompt. Cloning the repo does not run setup by itself; the agent starts
this interview when you first ask it to set up or make clips.

## What you'll need

The active agent reads the editorial documents and does the editing reasoning. **No Anthropic
or OpenAI API key is required.** These are the media-service connections:

| Connection | What it does | Get it from |
| --- | --- | --- |
| **AssemblyAI API key** | Primary transcript, word times, speaker labels and disfluencies | [AssemblyAI dashboard](https://www.assemblyai.com/dashboard) → API Keys |
| **Gemini API key** | Listens to source audio against the numbered transcript | [Google AI Studio](https://aistudio.google.com/apikey) → Create API key |
| **Groq API key** | Existing audit and ASR-fallback helpers; not primary transcription | [Groq Console](https://console.groq.com/keys) → Create API key |

For **Frame + Monday delivery**, also bring your own Monday personal API token and an Adobe
Frame.io OAuth app's Client ID/Client Secret, then sign into Frame in the browser. Setup saves
where each brand's shorts belong. [Exact steps and the complete optional-connection list](CONNECTIONS.md).

API usage is billed by each provider according to your account. The agent explains the selected
connections before processing footage. Enter your own media/delivery credentials. The dedicated read-only editorial-brain access is
already bundled; it requires no signup or token entry.

## Agent instructions — run this interview

Resolve `<plugin>` to `plugins/vibe-editing` in a checkout, or to `${CLAUDE_PLUGIN_ROOT}` in a
marketplace installation. In the command examples below, replace `plugins/vibe-editing` with
that resolved absolute directory. This guide and CONNECTIONS.md live in `<plugin>/docs/`;
they do not depend on the repository's root documents being present.


**Before the first production job**, run `python3 plugins/vibe-editing/onboard.py status`.
Read saved progress, then resume only missing steps. Do not ask the user to collect every key
before explaining what each is for. Do not print secrets or ask them to paste secrets into chat.
This setup requirement applies to using the product, not read-only questions or repo development.

### 1. Pick the work and install the tools

Ask: **“Which brands do you edit for, and are you mainly making shorts?”** Save separate brand
identities and default to shorts. Ask which editing backend they intend to use: preserve an
existing project choice; verify its tooling rather than silently substituting a backend.

Run the kit's doctor with its `.venv` Python. Install only missing dependencies from `INSTALL.md`.
The repository contains a portable headless runtime. The current team's native Premiere/Filmcraft
bridges need their own team-supplied installation/access; having
API keys does not install those tools. Mark missing companion tooling **pending**, never ready.

### 2. Connect the same editing stack

Walk through **AssemblyAI first**, then **Gemini**, using [CONNECTIONS.md](CONNECTIONS.md).
Cover Groq only for selected audit/fallback paths. The active model reads the bundled editorial
documents and makes cut/style decisions; never ask for an Anthropic/OpenAI key or require a
separate Claude CLI for normal editing. Team live-brain access is already bundled in `config/editorial-brain.json`; verify it with
`editorial_brain.py` without asking for a token. No separate local credential file is needed.

For each API key: open its account page, give the short click path, let the user enter it in
`onboard.py set <FIELD>`'s hidden interactive prompt, then run its read-only check. These commands
are for the agent to run in an interactive terminal panel; the user only enters the hidden value.
If the interface cannot accept private input, open the private local file for user entry and
resume after they save it. Never put a secret in command arguments, source code or `.mcp.json`.

Save only in `~/.config/vibe-editing/` with owner-only permissions. `onboard.py status` reports
presence, not values. `onboard.py verify <FIELD>` checks read access without processing footage.
Record **configured**, **verified**, **pending**, or **skipped** precisely; an unavailable model,
bridge stays pending even when a key is accepted. See the feature-specific checks
in CONNECTIONS.md. Never fetch someone else's existing machine credentials.

### 3. Connect delivery once

Ask: **“Will you deliver clips to Frame.io and Monday?”** If yes:

→ Use skill: `plugins/vibe-editing/skills/monday/SKILL.md`.

Reuse the brands already supplied. Ask for editor initials and each brand's Frame exports-folder
link + Monday board link. Verify the exact review groups, status labels and link columns. Default
to shorts. Frame sign-in and the Monday token are private, individual connections. Setup does
not authorize a test upload or changing a board.

If they also want Drive downloads, YouTube publishing, Buffer scheduling or Topaz, follow only
those sections in CONNECTIONS.md. These are extra capabilities, not additional primary-edit keys.

### 4. Apply their brand and check one real job

Ask for logo, fonts, caption reference, licensed music and any editing preferences in one short
brand interview. Use the existing brand folders and settings. Don't duplicate setup questions.

Show a compact checklist: **connection · configured/read-verified/pending/skipped · next step**.
Record feature progress with `onboard.py record <feature> --status <status>`. Never record
`editing: verified` until the intended backend is verified, the relevant editorial documents have been read, and
one user-supplied test job has produced a playable local result. A doctor pass or accepted API key
is not an end-to-end test. Ask for a source file/link when ready for that first job.

Finish by showing the local clip and commands they can say next:

- **“Make shorts from this video.”**
- **“Push these clips to Frame and Monday.”**
- **“Change the captions to match this reference.”**

Uploads, publishing and Topaz processing still require an actual request for the identified media.

## Resume or change setup

Say **“continue onboarding”**, **“add another brand”**, or **“update my API key.”** The agent checks
the saved progress and continues from there. Detailed credentials, paths, validation and optional
connections are maintained in [CONNECTIONS.md](CONNECTIONS.md); installation is in [INSTALL.md](INSTALL.md).
