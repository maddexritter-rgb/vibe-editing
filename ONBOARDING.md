# Vibe Editing — Setup

Turn your long videos into finished, captioned vertical clips — **in your own brand** — without
editing anything yourself. **You do not need to be technical.** The Claude Code app does the whole
setup for you and asks you a few simple questions along the way.

**Repo:** https://github.com/maddexritter-rgb/vibe-editing

---

## How to set it up — 3 steps (about 10–15 minutes, mostly just answering questions)

**1. Get the Claude Code desktop app** → **https://claude.com/claude-code**
Download it, open it, and sign in. (You need a paid Claude plan — Pro or Max.) This one app does everything — there's no other software to install.

**2. Start a new chat and paste the big box below.**
Copy **everything between "✂️ COPY FROM HERE" and "COPY TO HERE ✂️"** and paste it into the chat. (You can also just drop this whole document into the chat — Claude will know what to do.)

**3. Answer its questions.**
Claude installs what it needs, then asks about your brand — logo, fonts, colors, music, and how you like your clips. Answer in plain English, and **drag in files** (like your logo) when it asks. When it's done, it makes you a test clip.

> 💡 **Two things to expect:** (a) Claude may ask permission to install something or run a step — just click **Allow / Yes**. (b) It keeps a **checklist at the top of the chat** so you can watch it work through the setup.

---

### ✂️ COPY FROM HERE ⬇️

```
You're setting up "Vibe Editing" for me on this Mac. I am NOT technical — so do everything
yourself, never make me run a terminal command or edit a file by hand, and explain each step in
plain English. Keep a visible to-do list of the steps below and check them off as you go.

1) GET THE KIT
   - Clone it into my Documents folder:
       git clone https://github.com/maddexritter-rgb/vibe-editing.git
   - Then do all the work inside that "vibe-editing" folder.
   - If git or Homebrew isn't installed, install them first, yourself.

2) SET UP MY MACHINE (install only what's missing — everything here is free + open-source)
   - Run:  python3 plugins/vibe-editing/doctor.py   (it shows what's installed vs missing).
   - Install ONLY the missing pieces yourself: ffmpeg, yt-dlp, tesseract, rclone, and a Python
     virtual environment (.venv) with the kit's libraries + faster-whisper (offline transcription).
   - Re-run doctor.py until it prints READY, then tell me in plain English that it's ready.

3) HOW I'LL MAKE CLIPS (nothing for me to type)
   - I don't need to install or type any command for this. The folder has a CLAUDE.md that tells
     you how to run the pipeline, so I'll just say "make clips from this video" in plain English
     and you'll run it for me.
   - (OPTIONAL — only if I specifically want a /edit shortcut: give me these two lines to paste,
     one at a time:  /plugin marketplace add .  then  /plugin install vibe-editing@vibe-editing-marketplace)

4) FAST TRANSCRIPTION (free key — walk me through getting it)
   - I should get a free Groq key so transcription is fast. Give me simple click-by-click steps:
     go to console.groq.com, sign up free, create an API key, copy it.
   - Then ask me to paste it here, and you save it into the kit's keys file for me.
   - If I'd rather skip it, set the kit to use the free offline transcription instead.

5) MAKE IT MY BRAND (interview me — ask ONE question at a time and wait for each answer)
   - What's my brand / channel name?
   - Do I have a logo? (tell me to drag the image into this chat; put it on the clip end-card)
   - What caption font do I want? (show me a few from the ~50 free fonts included, or let me drop
     my own font file)
   - What caption look? (ask me to paste a screenshot of a caption style I like, and match the
     font + colors + emphasis to it)
   - Do I have music? (let me drop royalty-free tracks, or use the defaults)
   - What are my videos about, how should a clip OPEN (the hook), and how should it END?
   - Any editing preferences? (tighter cuts, bigger captions, don't open on a question, etc.)
   - Then apply ALL my answers to the kit's settings + editing rules, and show me what changed.

6) MAKE A TEST CLIP
   - Ask me for one video (a YouTube link, or a file on my computer), run the edit on it, and show
     me the finished clip (it lands in the 20_DELIVER folder). Ask what I'd change, adjust, and
     re-run until I love it.

7) (OPTIONAL) UPLOAD TO FRAME + MONDAY
   - Ask: "Want to connect Frame.io and Monday so I can deliver your clips for review?"
   - If yes, use plugins/vibe-editing/skills/monday/SKILL.md and its onboarding reference.
     Keep it short: brands, mostly shorts (default yes), editor initials, then each brand's
     Frame exports-folder link + Monday board link. Walk me through my own Frame browser
     sign-in and Monday API token; enter secrets privately, never in chat. Save the routes.
   - Setup only connects and checks access. Upload when I say "push these clips to Frame
     and Monday"; use the saved brand route and correct filenames.

8) (OPTIONAL) STRONGER AUDITS ON LONG VIDEOS
   - The quality checks already run automatically. If I want the best results on long (>3 min)
     videos, offer to also set up the free Gemini "watch" add-on, and walk me through that key too.

Throughout: be patient, assume I've never used a terminal, and tell me what's happening in plain
English. Only use this kit — don't pull tools or keys from anywhere else on my computer.
```

### ⬆️ COPY TO HERE ✂️

---

## Once it's set up

- **Make clips anytime:** open Claude Code in the `vibe-editing` folder and just say *"make clips from this"* with a YouTube link or a video file — Claude already knows what to do, no command needed. *(An optional `/edit` shortcut exists if you set it up.)*
- **Want longer horizontal "mid" videos for subscriber growth?** (16:9 clips from a Q&A / podcast, not 9:16 shorts) — say *"mine highlights from this"*. It ranks the strongest moments, cuts them, and appends your own outro if you've put one in `brand/cta/`. To title + schedule them to your channel afterward, say *"post and schedule these"* (connects to your own YouTube).
- **Want to auto-post your finished shorts on a schedule?** Say *"fill my queue"* — it queues your 9:16 shorts to your channel through Buffer, hands-off. *(This one needs your own Buffer account + a storage bucket; the skill's `buffer/README.md` walks you through it.)*
- **Deliver for review:** say *"push these clips to Frame and Monday"*. First use walks you through your own account connections and saves a destination for each brand. Shorts are the default; filenames and Monday review links are handled for you.
- **Your finished clips** show up in that project's **`20_DELIVER/`** folder.
- **Change anything later** — just tell Claude in plain English: *"make the captions bigger," "use this new logo," "cut it tighter," "don't open on a question."* It updates itself.
- **Want the deeper walkthrough?** Open **`Vibe-Editing-Playbook.pdf`** in the same folder.

---

<details>
<summary><strong>For the technical / curious — what Claude is doing under the hood (you can ignore this)</strong></summary>

**The tools it installs** (all free, open-source — Claude installs only what's missing):

| Tool | What it's for |
|---|---|
| **Homebrew** | Mac package manager (only if you don't already have it) |
| **ffmpeg** | the engine — cuts, encodes, reframes, extracts frames |
| **yt-dlp** | pulls a video down from a YouTube / URL link |
| **tesseract** | OCR — the caption audit reads your burned-in captions to verify them |
| **rclone** | *(optional)* pulls footage from a Google Drive link |
| **Python 3 + a local `.venv`** | runs the pipeline scripts (OpenCV, NumPy, etc.) |
| **faster-whisper** | offline transcription so it works with **no API key** |

**Keys** (in `plugins/vibe-editing/config/keys.env`): `GROQ_API_KEY` (free, fast transcription — recommended), `ANTHROPIC_API_KEY` (optional, most reliable caption styling), `GEMINI_API_KEY` (optional, long-video "eyes").

**Enable the plugin manually:** `/plugin marketplace add .` then `/plugin install vibe-editing@vibe-editing-marketplace`.

**Brand levers, by hand** (under `plugins/vibe-editing/`):

| To change… | Edit |
|---|---|
| Caption colors / size / emphasis | `skills/caption-clips/presets/spice.json` |
| Caption font | the bundled `skills/caption-clips/fonts/` (see `FONTS.md`), or drop your own, then set it in the preset |
| What makes a clip worth cutting (your SOP) | `skills/edit/prompts/clip_select.md` + `references/editorial_sop.md` |
| Music | drop tracks in `brand/music/` (set `VIBE_MUSIC=/your/music`) |

**The 6 quality gates** (run automatically inside `/edit`): mechanics · narrative · visual (face/framing) · audio (levels) · captions (accuracy/timing) · script (cold-viewer test). A clip that fails one doesn't ship. Optional Gemini "eyes" for long-form: free key at https://aistudio.google.com/apikey, add the `claude-video-vision` MCP, then `video_configure backend=gemini-api`.

*Windows: install [WSL](https://learn.microsoft.com/windows/wsl/install) first, then do everything inside your WSL terminal.*

</details>
