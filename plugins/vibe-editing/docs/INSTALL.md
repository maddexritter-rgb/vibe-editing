# Install — Vibe Editing

A portable Claude Code plugin. It does **not** need to live in `~/.claude/skills/` — it runs
from wherever Claude Code installs it.

## 1. Add the marketplace + install the plugin

This folder is both the marketplace and the plugin. In Claude Code:

```text
/plugin marketplace add /absolute/path/to/vibe-editing-starter
/plugin install vibe-editing@vibe-editing-marketplace
```

Verify it loaded:

```text
/plugin        # shows vibe-editing as enabled
/edit          # the orchestrator entry point
```

(To iterate without the marketplace step, drop `plugins/vibe-editing/` into a skills directory;
it carries its own `.claude-plugin/plugin.json` and self-loads. Run `/reload-plugins` after edits.)

## 2. System tools

- **FFmpeg with libass** — REQUIRED. Every render shells out to `ffmpeg`/`ffprobe`; libass burns
  the captions. `brew install ffmpeg`
- **tesseract** — caption OCR used by the audit gates. `brew install tesseract`
- **yt-dlp** — URL ingest (installed by `requirements.txt`, or `brew install yt-dlp`).
- **rclone** — cloud-drive ingest (`footage-fetch`). `brew install rclone && rclone config`.
- Optional: **Node + npm** (only the `promo` end-card), **Montreal Forced Aligner** (`script-cut`).

## 3. Python dependencies (3.10+)

```bash
cd plugins/vibe-editing
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Heavy / optional (only if you use these paths):

```bash
pip install faster-whisper   # local transcription, no API key needed
pip install torch            # diarization / alignment
pip install mediapipe        # face-mesh reframe path
```

## 4. Run guided onboarding

Open this repo in your agent and say **“Set up Vibe Editing for me.”** Follow [ONBOARDING.md](ONBOARDING.md).
The current `/edit` stack uses AssemblyAI for primary transcription, Gemini for source-listener
notes. The active agent reads the bundled editorial documents; no Anthropic/OpenAI key is
required. Groq is only for selected audit/fallback paths. [All credentials and exact setup steps](CONNECTIONS.md).

The agent runs `python3 plugins/vibe-editing/onboard.py set ASSEMBLYAI_API_KEY` in an interactive
terminal so you can enter your key privately, then repeats for selected connections. Real values
are stored outside the repo and auto-loaded; `config/keys.env.example` contains empty examples.
Never put a real key in tracked files or agent chat. `onboard.py status` reports presence only.

## 5. Your assets

| Asset | Default | How to change |
|---|---|---|
| Caption font | free **Montserrat** bundled in `skills/caption-clips/fonts/` | swap your own — see `fonts_README.md` |
| Music library | none bundled | put royalty-free tracks in a folder, set `VIBE_MUSIC=/your/music` |
| Editorial taste | placeholder SOPs / prompts | fill in `skills/*/references/` and `skills/*/prompts/` |

## 6. Paths — how the plugin finds its own files

Everything resolves from **`${CLAUDE_PLUGIN_ROOT}`** (the install dir). Python scripts also
self-locate by walking up to the `.claude-plugin/` marker, and honor `VIBE_PIPELINE_ROOT` /
`CLAUDE_PLUGIN_ROOT`. **No absolute path or env var is required for scripts.**

## 7. Run it

`/edit` accepts a **local file, a URL, or a cloud-drive link**, then runs the spine
(ingest → scaffold → source-intel → detect → transcribe → mine → pick → validate → cut → QC →
render → re-QC → audit → deliver). Or drive the render engine directly once a project +
`manifest.json` exist:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/skills/render/engine.py" <project_dir>          # build
python3 "$CLAUDE_PLUGIN_ROOT/skills/render/engine.py" <project_dir> --bump   # revise (changed stages only)
```

## 8. First run

Test one short clip end-to-end and confirm: ffmpeg + libass present, AssemblyAI connected and the selected backend available, a font in `fonts/`, and a music folder set. After that, batches just work.
