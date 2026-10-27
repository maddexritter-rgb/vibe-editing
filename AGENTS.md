# Vibe Editing — agent entrypoint

For the first setup or production request (make/edit/deliver clips), read `ONBOARDING.md` and run
`python3 plugins/vibe-editing/onboard.py status` before starting production. Resume missing setup
one question at a time. Keys and access follow the **current `/edit` stack** documented in
`docs/CONNECTIONS.md`: AssemblyAI primary transcription, Gemini listener, team editorial-brain
read access, and the documented audit/script helper keys. Do not invent substitute providers.
This interview is not required for read-only questions or work on the repository's code/docs.

Do the setup work for the user. Secrets go into private hidden prompts or private local files,
never chat, command arguments, tracked files, screenshots or logs. Mark configured, verified,
pending and skipped precisely. Missing team bridges/playbooks are pending dependencies; an
accepted API key is not proof the native editing pipeline is installed.

Then read `CLAUDE.md` for workflow routing. Use only this kit and the user's own credentials.
Making clips does not authorize external uploads, social publishing, or Topaz processing.
