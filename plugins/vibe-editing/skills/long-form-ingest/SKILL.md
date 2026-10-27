---
name: long-form-ingest
description: Transcribe a long-form video to word-level timestamped JSON and detect scene boundaries. Use whenever a new video file enters the pipeline and a transcript does not yet exist. Produces transcript.json (word-level with timestamps), scenes.json (visual cut points), and meta.json (duration, fps, resolution). Every other video skill depends on these outputs.
---

# Long-form ingest

Use the current `/edit` transcription stack: **AssemblyAI first**, with disfluencies and speaker
labels, then a **Gemini listener map** on the same source audio and numbered words. The active
agent owns editorial decisions. Do not substitute Groq/local transcription for primary ingest
without labeling its `PARTIAL` evidence and verifying consequential words.

Read [onboarding](../../docs/ONBOARDING.md) first if connections are missing. Keys load privately from
`~/.config/vibe-editing/keys.env`. No keys ship in this plugin.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/long-form-ingest/scripts/transcribe_assemblyai.py \
  <source-dialogue> --out <project>/10_WORK/ingest/raw_transcript.v1.json
python3 ${CLAUDE_PLUGIN_ROOT}/skills/long-form-ingest/scripts/delivery_map.py \
  --words <project>/10_WORK/ingest/raw_transcript.v1.json --audio <source-dialogue> \
  --out <project>/10_WORK/ingest/delivery_map.v1.json --cache-dir <project>/10_WORK/ingest/gemini-cache
```

The shared `lib/_shared/asr.py` uses the live pipeline's editorial profile. Save the full provider
response, engine/model/profile/evidence status, and word/speaker evidence. Timestamps normalize
from AssemblyAI milliseconds to seconds; they locate words, not frame-certified edit points.
Reuse a current transcript rather than paying for it again. The ingest command saves the accepted
paid job in an adjacent private `.job.json`; after interrupted polling, repeat the same command
and output path to resume its ID. An unknown submit outcome requires reconciliation, not a fresh
receipt or blind resubmission. The listener's unavailable chunks
remain recorded; a missing Gemini note never approves, rejects or rewrites a cut.

`transcript.json` contains flat `words` and `segments`/`utterances`, with `word`, `start`, `end`,
`confidence`, and `speaker` on word records. Preserve the source clock and stable word order.
Scene and technical metadata can be collected with the helpers below. Use `transcribe_local.py`
only for an explicitly selected offline fallback; it is not the standard onboarding route.

## scenes.json schema

```json
{
  "scenes": [
    {"start": 0.0, "end": 128.4, "type": "static"},
    {"start": 128.4, "end": 245.1, "type": "cut"}
  ]
}
```

## scripts/scenes.py

```python
#!/usr/bin/env python3
import sys, json
from scenedetect import detect, ContentDetector

video_path, out_path = sys.argv[1], sys.argv[2]
scene_list = detect(video_path, ContentDetector(threshold=27.0))

out = {
    "scenes": [
        {"start": s[0].get_seconds(), "end": s[1].get_seconds(), "type": "cut"}
        for s in scene_list
    ]
}

# If no scenes detected, treat whole video as one
if not out["scenes"]:
    import subprocess
    dur = float(subprocess.check_output([
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "csv=p=0", video_path
    ]).decode().strip())
    out["scenes"] = [{"start": 0.0, "end": dur, "type": "static"}]

with open(out_path, "w") as f:
    json.dump(out, f, indent=2)

print(f"Found {len(out['scenes'])} scenes")
```

## Local requirements

FFmpeg/ffprobe extract and inspect source audio. Scene detection additionally needs PySceneDetect.
Cloud ASR uses `httpx` (included in requirements). Local MLX fallback needs `mlx-whisper` installed
explicitly on a compatible Mac; never claim an unavailable fallback ran.
