# Vibe Editing — agent entrypoint

For the first setup or production request (make/edit/deliver clips), read `ONBOARDING.md` and run
`python3 plugins/vibe-editing/onboard.py status` before starting production. Resume missing setup
one question at a time. Keys and access follow the **current `/edit` stack** documented in
`docs/CONNECTIONS.md`: AssemblyAI primary transcription, Gemini listener, and selected audit/fallback
connections. The active agent reads the bundled editorial documents and makes the cut/style
decisions. Never ask for an Anthropic/OpenAI key or a separate Claude CLI. Team live-brain access
is provisioned once rather than asking each editor to create a token. Do not invent substitute providers.
This interview is not required for read-only questions or work on the repository's code/docs.

Do the setup work for the user. Secrets go into private hidden prompts or private local files,
never chat, command arguments, tracked files, screenshots or logs. The deliberately bundled
read-only editorial-brain token in `config/editorial-brain.json` is the sole shared-access exception;
use it automatically and never substitute a database-admin or personal provider credential. Mark configured, verified,
pending and skipped precisely. Missing team bridges/playbooks are pending dependencies; an
accepted API key is not proof the native editing pipeline is installed.

Then read `CLAUDE.md` for workflow routing. Use only this kit, the deliberately bundled team reader, and the user's own provider credentials.
Making clips does not authorize external uploads, social publishing, or Topaz processing.
