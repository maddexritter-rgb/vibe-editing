# Keys and account connections

This inventory comes from the **current canonical `/edit` workflow and its owning skills**,
not the starter kit's older Groq-first examples. It also lists the additional capabilities
shipped by this repo. Everyone gets their own provider keys and authorized team access.
Never distribute the author's keys or a Supabase admin key to new editors.

## Editing stack

| Provider / field | Role in the actual pipeline | Setup / check |
| --- | --- | --- |
| `ASSEMBLYAI_API_KEY` | Primary `editorial_ingest` ASR, with speaker labels and disfluencies | Required for standard source ingest; read check below |
| `GEMINI_API_KEY` | Source-audio delivery map / listener notes; also supports optional video watching | Standard source-listener connection; missing chunks are recorded unavailable, not fabricated |
| `EDITORIAL_BRAIN_READ_KEY` | Function-scoped shared-playbook reader | Team-provisioned; not a generic Supabase service key |
| `EDITORIAL_BRAIN_FUNCTION_URL`, `EDITORIAL_BRAIN_PROJECT_REF` | Bind that reader to the intended brain | Configuration, not API keys; supplied by team admin |
| `GROQ_API_KEY` | Audit ASR, Whisper availability fallback, and some legacy scripted LLM helpers | Cover during setup; needed for those cloud helpers, not the primary dialogue transcript |
| `ANTHROPIC_API_KEY` | Scripted LLM helpers, caption director / optional scorecards | Some helpers fall back to a signed-in `claude` CLI; check the actual path before declaring it ready |
| Agent account / native bridge | Active agent designs cuts; Premiere Agent or Filmcraft executes the chosen native route | Login/install connection, not an extra OpenAI API key |

### AssemblyAI — do this first

1. Open [AssemblyAI dashboard](https://www.assemblyai.com/dashboard) and sign into your account.
2. Open **API Keys**, create a key for this work, and copy it privately.
3. Agent runs `python3 plugins/vibe-editing/onboard.py set ASSEMBLYAI_API_KEY`; user enters the
   value at the hidden prompt. Then run `.../onboard.py verify ASSEMBLYAI_API_KEY`.

The read check lists at most one transcript and prints no transcript text. It proves read
access, not available credit or successful transcription. The first user-supplied clip is the
processing test. The shared editorial profile preserves the full provider response and normalizes
millisecond word times to seconds; fallback output remains `PARTIAL`.
[Official key instructions](https://www.assemblyai.com/docs/faq/how-to-get-your-api-key),
[pre-recorded API](https://www.assemblyai.com/docs/pre-recorded-audio/api-reference/transcripts/submit).

### Gemini — source listener

Open [AI Studio API keys](https://aistudio.google.com/apikey), sign in and create a key in your
project. Save with `onboard.py set GEMINI_API_KEY`, then `onboard.py verify GEMINI_API_KEY`.
The model-list check verifies authentication only. Check that the actual listener model is
available to that account before reporting the listener ready; record unsupported models as
unavailable. The shipped listener uses the current pipeline's bounded/cached model chain.
A provider key does not install the separate video-vision MCP add-on; only configure that add-on
if its tool is actually installed. Footage sent to cloud ASR/listeners leaves the local machine.
[Official key instructions](https://ai.google.dev/gemini-api/docs/api-key).

### Shared editorial brain — team access, not a self-serve signup

Ask the team's admin for **the function URL, project reference and function-scoped read token**.
Save through `onboard.py set EDITORIAL_BRAIN_FUNCTION_URL`, `... PROJECT_REF` and `... READ_KEY`.
Launch the team's installed canonical reader through `onboard.py run -- python3 <reader> ...`
so it inherits the private environment, and run a read-only playbook preflight and verify the
intended project and returned active playbooks. No new database, schema or admin token is needed.

The starter repository does not bundle the private shared database, its contents, the complete
current `assembly-design` package, or native editor bridges. If those companion tools/access
are missing, record them as pending and tell the user which team installation is needed.
Do not call this a verified replica of the current native `/edit` pipeline just because keys
have been saved. Public portable rendering and team-native editing are distinct capabilities.

### Groq — audit/fallback connection

[Groq Console](https://console.groq.com/keys) → **API Keys → Create API Key**.
Save with `onboard.py set GROQ_API_KEY`, check with `onboard.py verify GROQ_API_KEY`.
The read check lists models without processing media. AssemblyAI stays primary even when both
keys exist. A fallback transcript is not equivalent speaker/disfluency evidence.
[Official quickstart](https://console.groq.com/docs/quickstart).

### Anthropic — scripted LLM helpers

[Claude Console](https://platform.claude.com/settings/keys) → **Create key**.
Save with `onboard.py set ANTHROPIC_API_KEY`, check with `onboard.py verify ANTHROPIC_API_KEY`.
API usage is billed separately from a chat subscription. If using an existing signed-in Claude
Code fallback, verify that the specific helper supports it. Do not demand an API key merely
for the active agent to reason about the edit.
[Official key instructions](https://platform.claude.com/docs/en/get-api-key).

## Frame.io + Monday delivery

→ Use skill: [`monday`](../plugins/vibe-editing/skills/monday/SKILL.md), including its
[account walkthrough](../plugins/vibe-editing/skills/monday/references/onboarding.md).

| Credential / configuration | Source |
| --- | --- |
| Monday personal API token | Profile picture → Developers → API token → Show → Copy |
| Adobe Client ID + Client Secret | Developer Console → Frame.io API → OAuth Web App |
| Frame OAuth access/refresh tokens | Browser sign-in; helper saves and refreshes them |
| Each brand's Frame account/project/folder + Monday board/group/column IDs | Resolve from user's links; these are destinations, not secrets |

Files remain under `~/.config/vibe-editing/delivery/`; never move them into the repo. Onboarding
only reads identity/schema. It does not upload anything or create test board rows.

## Additional capabilities (only connect when selected)

### Google Drive footage

A browser OAuth login through **rclone**, not an API key to paste. Run interactive `rclone config`,
create `gdrive`, choose Google Drive and **read-only** access, then let the user sign into the
account that can read their source folder. A custom Google client ID/secret is optional, not a
mandatory signup. Verify using a read of the user's selected folder; never use `rclone config show`
to print tokens. Tokens stay in rclone's private config. [Official setup](https://rclone.org/drive/).

### YouTube titles, drafts and scheduling

Use the user's own **Google OAuth Desktop app JSON**, then browser sign-in. A plain YouTube API
key is insufficient to upload/change videos. In [Google Cloud Console](https://console.cloud.google.com/):
create/select a project, enable **YouTube Data API v3**, configure the OAuth consent screen
(and add the user as a test user while in Testing), then create a **Desktop app** OAuth client
and download its JSON. Save it privately in `~/.config/vibe-editing/youtube/`.

Install `google-api-python-client` and `google-auth-oauthlib` only for this feature. Pass the
private `--client-secret` and `--token` paths explicitly to the bundled highlight helpers;
use separate token files for distinct scopes/accounts. Authenticate without uploading by calling
`highlight_post.get_service(client_secret, token_path)` and reading `channels().list(part='id,snippet',
mine=True)`. Show the exact channel before any future publishing. Setup does not authorize
scheduling or uploading. [Official Google setup](https://developers.google.com/youtube/data/quickstart/python).

### Buffer scheduling and public video hosting

**Buffer token:** [Buffer settings → API](https://publish.buffer.com/settings/api) → create a
personal API key. Save `BUFFER_TOKEN` privately. Run the bundled `fill_queue.py discover`,
confirm the intended channel and save `BUFFER_CHANNEL_ID` (an ID, not another key).

**Hosting:** Buffer needs a reachable video URL. For the bundled Supabase uploader, the user's
own `SUPABASE_URL`, `SUPABASE_KEY` and `SUPABASE_BUCKET` are needed. The helper currently expects
a legacy `service_role` JWT in its Bearer header. Store that privileged credential only on the
user-controlled machine; never share it with recipients or put it in frontend code. New
`sb_secret_*` keys require adapting that legacy header path before claiming compatibility.
Use a dedicated delivery bucket: public media is publicly retrievable. If only a new-style
key is available, mark local uploads pending; existing public URLs can use `--video-url`.
This is separate from editorial-brain read access. The bundled uploader speaks Supabase's
Storage REST API, not generic S3; another host requires its own adapter or pre-hosted URLs.

Verify Buffer with channel discovery and hosting with a read of the chosen bucket. A dry-run
is a schedule preview, not authorization to publish or a successful upload test.
[Buffer key setup](https://support.buffer.com/en-us/articles/how-to-create-your-buffer-api-key-ShIgYVwM6j),
[Supabase key types](https://supabase.com/docs/guides/getting-started/api-keys),
[public buckets](https://supabase.com/docs/guides/storage/buckets/fundamentals).

### Topaz enhancement

The current canonical `/edit` supports `TOPAZ_API_KEY`, enabled **only for explicitly requested
Topaz processing**. Obtain it through the user's [Topaz developer account](https://developer.topazlabs.com/).
It is not needed for ordinary cuts and saving it does not authorize processing. The portable
starter does not include the full native Topaz execution route; keep it pending until the
team's owning integration is installed and verified. No smoke-test enhancement during onboarding.

## Storage, runtime and readiness

`onboard.py set <FIELD>` uses hidden input, preserves existing configured fields, and atomically
saves mode-0600 `~/.config/vibe-editing/keys.env` under a mode-0700 directory. The runtime loads
this automatically. Explicit environment values take precedence. Legacy local `config/keys.env`
remains supported and ignored by Git; the distributed file is now `keys.env.example`.

Frame/Monday use their dedicated private JSON files. YouTube and rclone own their OAuth token
files. Never paste secrets into an agent conversation, commit them, or hardcode them in MCP
configuration. For MCP servers that require an environment variable, pass it through the host's
private environment and restart that server after saving; Python's key loader cannot update an
already-running MCP process.

`onboard.py run -- <helper> ...` passes configured values to child helpers without putting them
in command arguments or changing the parent agent's environment. Use it for external companion
readers and host setup tools; never use it to dump an environment.

`onboard.py status` reports credential **presence only**. `verify` uses a read-only provider
request and prints no response body. Feature records are progress notes, not automatic claims
of testing. Record a pending dependency rather than calling it verified. The doctor checks
local tools and key presence; it does not log into any remote account.

No bundled runtime consumes **ElevenLabs**, **OpenAI API**, **Deepgram**, or **Hugging Face** keys.
Do not ask new editors to obtain them. The `openai` Python package here is used as a Groq-compatible
client and does not imply an `OPENAI_API_KEY` requirement. Any future integration must update
this inventory and its owning setup check together.
