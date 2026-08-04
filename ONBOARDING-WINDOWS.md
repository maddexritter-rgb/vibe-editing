# Vibe Editing — Windows Setup (community port guide)

Turn long videos into finished, captioned vertical clips — in **your own brand** — on a
regular Windows PC. No editing software, no terminal knowledge. Claude Code does everything
and interviews you about your brand along the way.

This is the **Windows-native** version of the kit's onboarding (the original was written for
Mac). It bakes in every fix discovered during a real Windows port (2026-08), so your setup
should work the first time.

**You need:** Windows 10/11 · the [Claude Code desktop app](https://claude.com/claude-code)
(paid plan) · ~10 GB free disk · 15–30 minutes, mostly answering brand questions.

**How:** open Claude Code, start a chat, and paste everything in the box below. Answer its
questions in plain English. Drag in files (logo, style guide) when it asks.

---

### ✂️ COPY FROM HERE ⬇️

```
You're setting up the "Vibe Editing" video kit for me on Windows 11, NATIVE (no WSL, no
Homebrew, no bash). I am NOT technical — do everything yourself, explain each step in plain
English, keep a visible to-do list, and never make me use a terminal.

1) GET THE KIT
   Install git via winget if missing (Git.Git), then:
   git clone https://github.com/maddexritter-rgb/vibe-editing.git
   into my Documents folder. Work inside that folder from then on.

2) READ BEFORE INSTALLING
   Read README.md, INSTALL.md, CLAUDE.md, and plugins/vibe-editing/doctor.py.
   setup.sh is a Mac bash script — read it for intent, NEVER run it.

3) INSTALL TOOLS (verified winget IDs — confirm with `winget search` if unsure)
   - Gyan.FFmpeg            (the FULL build — it has libass captions + hardware encoders)
   - yt-dlp.yt-dlp
   - tesseract-ocr.tesseract  (its installer forgets PATH — add C:\Program Files\Tesseract-OCR
                               to my user PATH yourself)
   - Rclone.Rclone
   - Python.Python.3.12
   Then create the venv at plugins/vibe-editing/.venv and install requirements.txt
   PLUS faster-whisper, PLUS pin "opencv-python<5" (OpenCV 5 removed APIs the kit uses).
   NOTE: winget PATH changes don't reach your current session — refresh PATH from the
   registry in each command, or have me restart the app once.
   Run plugins/vibe-editing/doctor.py and fix until it prints READY.

4) HARDWARE ENCODING (detect, don't assume)
   Run `ffmpeg -encoders` and find my hardware H.264 encoder: h264_amf (AMD),
   h264_nvenc (NVIDIA), or h264_qsv (Intel). Then port the kit's encoder policy:
   hardware encoder for proxy/intermediate renders, libx264 for the final delivered file.
   The single source of truth is plugins/vibe-editing/lib/_shared/fast_encode.py — but
   h264_videotoolbox (Apple-only) is ALSO hardcoded in lib/_shared/faded_trim_cut.py,
   lib/_shared/testimonial_reframe.py, skills/render/stages/{cut,reframe,grade,mix,leadfix}.py,
   and skills/promo/templates/glass/assemble.py. Fix every one or renders WILL crash.
   Transcription: faster-whisper on CPU with compute_type=int8 and cpu_threads = my
   physical core count. (If I have an NVIDIA GPU, use device=cuda + float16 instead.)

5) KNOWN WINDOWS FIXES (all real crashes from the first Windows port — apply proactively)
   a. Subprocess calls use 'python3' (~72 spots, 19 files) → replace with sys.executable.
   b. Hardcoded /tmp/ paths (8 files) → tempfile.gettempdir().
   c. The "portable path bootstrap" block pasted into ~42 .py files uses _pl.Path but never
      imports pathlib as _pl → add `import pathlib as _pl` wherever _pl is used.
   d. ffprobe csv output can trail a comma on Windows ("30/1," / "194,") → strip ",\n" before
      int()/float() parsing (render/stages/_util.py ffprobe_fps + duration, cut.py frame
      counter, reframe.py frame counter).
   e. ffmpeg filter-graph file paths (ass=/subtitles= in caption burn,
      skills/caption-clips/scripts/generate_spice.py) → convert \ to / AND escape the drive
      colon (C\:) or the burn fails with "Could not create a libass track".
   f. caption_director.py falls back to `zsh -ic` to find API keys → guard with
      os.name != "nt" or it crashes FileNotFoundError.
   g. Mac system fonts (/System/Library/Fonts) in skills/watch/scripts/_util.py and
      skills/edit/scripts/contact_sheet.py → prepend C:\Windows\Fonts\segoeui.ttf / arial.ttf.
   h. doctor.py prints brew install advice → make it print winget commands on win32.
   i. reqc.py hard-requires 2160x3840 → also accept 1080x1920 (correct for wide-shot sources).
   j. window_validator.py chokes on Groq transcripts ("segments": null) → fall through to
      the "words" list when segments is empty.
   After porting: compile-check every kit .py file (py_compile) and prove the encoder policy
   by rendering a 2-second test video through the hardware encoder.

6) TRANSCRIPTION — set up BOTH paths
   Walk me through getting a free Groq key (console.groq.com → API Keys → Create → paste it
   here) and save it in plugins/vibe-editing/config/keys.env. That file is NOT gitignored by
   default AND is git-tracked — add it to .gitignore AND run
   `git update-index --skip-worktree` on it so my key can never be uploaded.
   Also verify local faster-whisper actually transcribes (generate speech with Windows TTS
   and transcribe it) so I'm never stuck if Groq rate-limits.

7) REGISTER THE PLUGIN so /edit works
   In the desktop app there's no /plugin dialog — write it into my user settings file
   (~/.claude/settings.json):
     "extraKnownMarketplaces": {"vibe-editing-marketplace": {"source": {"source":
       "directory", "path": "<absolute path to the vibe-editing folder>"}}},
     "enabledPlugins": {"vibe-editing@vibe-editing-marketplace": true}
   Then tell me to restart Claude Code once.

8) MAKE IT MY BRAND (interview me — ONE question at a time, wait for each answer)
   FIRST ask if I have a brand style guide document (PDF/anything) — if I drag one in, read
   it and extract name/colors/fonts/logo rules from it instead of quizzing me. (If it's a
   PDF, you may need `winget install oschwartz10612.Poppler` to read it.) Then only ask
   what's missing:
   - Caption look? (design from my brand, or match a screenshot I paste)
   - Music? (my royalty-free tracks / none)
   - My topics + how a clip should OPEN (hook) and END (CTA? end-card with my logo?)
   Apply it: caption preset is skills/caption-clips/presets/spice*.json — my font goes in
   with EACH WEIGHT renamed to its own font family (use fonttools; that's how the bundled
   fonts work), fonts_dir pointed at it, my accent color as the emphasis/guest color. Logo
   PNGs into brand/logos/. Compose end-cards as a full PNG in PIL (ffmpeg overlay-on-color
   leaves a faint box). Add my audience/topics to skills/edit/prompts/clip_select.md as an
   appended BRAND CONTEXT section — never rewrite its data-backed lift rules.
   Show me a rendered sample frame of my caption style before we proceed.

9) TEST CLIP (small first)
   Ask me for ONE short video (2–3 min). Run the full pipeline per the edit skill's spine —
   transcribe, mine, hand-cut, validate, render, and run the QC gates on the delivered file.
   VERIFY FRAMES BY LOOKING AT THEM: extract stills across every segment and check the face
   is never cut off (if the subject walks/moves, set reframe y_scope:"segment" in the
   manifest). Show me the clip, ask what I'd change, and re-render until I love it.

Throughout: be patient, assume I've never used a terminal, verify by measurement (render and
look at real frames) rather than assuming, and tell me honestly when something fails.
```

### ⬆️ COPY TO HERE ✂️

---

## After setup

- Say **"make clips from this"** with a YouTube link or video file — that's the whole workflow.
- Change anything in plain English: "captions bigger", "cut tighter", "new logo".
- Optional extras to ask Claude for later: **B-roll cutaways** (sparse, ~2s, from your own
  footage library), a free **Gemini key** for long-video quality checks (note: that uploads
  footage to Google — decide per video), an **Anthropic API key** for the smartest caption
  emphasis.

*Guide by the QEST4 team's Windows port, 2026-08. The kit itself: github.com/maddexritter-rgb/vibe-editing*
