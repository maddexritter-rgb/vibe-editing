#!/usr/bin/env python3
"""Assembly-level coherence validation.

Ports CLIPPER-3.0's validate-assembly action (src/app/actions/validate-assembly.ts).
Reads a list of final clip transcripts (plus surrounding cut context), asks
Claude to flag any clip that would confuse a viewer watching the sequence
back-to-back — incomplete first/last sentences, contextless references,
orphaned filler.

This is DIFFERENT from review_clip.py, which audits clips one at a time.
Assembly validation catches boundary failures that only surface when the
clips are viewed as a continuous sequence.

Input manifest (JSON):
  {
    "clips": [
      {"label": "A", "text": "...kept words of final clip...",
       "before": "...last ~20 words cut from before this clip (optional)",
       "after":  "...first ~20 words cut after this clip (optional)"},
      ...
    ]
  }

Output: assembly_review.md + JSON with {remove_clips, flag_clips} indices.

Requires: ANTHROPIC_API_KEY or GROQ_API_KEY in env.
"""
from __future__ import annotations

# ── engine bundled-keys autoload (config/keys.env) ──
import os as _ko, pathlib as _kp
def _acq_load_keys():
    d = _kp.Path(__file__).resolve()
    for p in (d, *d.parents):
        if (p / ".claude-plugin").is_dir():
            import sys as _ks
            _ks.path.insert(0, str(p))
            from vibe_credentials import load_keys
            load_keys()
            return
_acq_load_keys()
# ── end keys ──
import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


ANTHROPIC_MODEL = "claude-sonnet-4-6"
GROQ_MODEL = "llama-3.3-70b-versatile"
MODEL = ANTHROPIC_MODEL
CHUNK_SIZE = 50
CHUNK_OVERLAP = 2

# Ported verbatim from CLIPPER-3.0 src/app/actions/validate-assembly.ts.
SYSTEM_PROMPT = """You are a video editor reviewing the final cut of a short-form clip. The numbered clips below will play back to back as a continuous video — the viewer sees ONLY these clips with no other context. There is no text on screen, no titles, no narrator — just these spoken words in sequence.

Flag any clip that would confuse or jar a viewer. Specifically:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INCOMPLETE THOUGHTS — ALWAYS FLAG
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
This is the most important check.

For EVERY clip, read its FIRST sentence and its LAST sentence independently. Ask: does each one express a complete thought on its own?

INCOMPLETE ENDINGS — if the last sentence of a clip would not stand alone as a finished thought, the clip is BROKEN. Flag it regardless of how good the rest of the clip is:
  "And if I don't solve this,"         ← conditional with no resolution → BROKEN
  "So I would say that realistically," ← trails off mid-thought → BROKEN
  "We ended up getting a lot of"       ← missing its object → BROKEN
  "So we are at 530"                   ← number with no unit or context → BROKEN
  "I honestly think you"               ← verb with no object → BROKEN

INCOMPLETE STARTS — if the first sentence continues a thought that was removed:
  "is stopping us is that"             ← no subject → BROKEN
  "of our annual revenue in 65%"       ← starts mid-phrase → BROKEN
  "Or I am overstuffed in winter."     ← "Or" continues a removed sentence → BROKEN

CONTEXTLESS REFERENCES — ALWAYS FLAG
- References to something never established in the kept clips ("that method", "step two" when step one was cut)
- Numbers or names that only make sense with removed context

FILLER — ALWAYS REMOVE
- Clips that are entirely filler with no substantive content ("Okay.", "Mhmm.", "Yeah.", "Perfect.", "Alright.")
- Clips under 3 words that carry no meaning on their own

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DO NOT FLAG just because:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- The speaker uses informal grammar that still communicates a complete thought
- The speaker uses filler words WITHIN a larger substantive sentence
- The sentence structure is complex or run-on but still resolves

The key distinction: CASUAL SPEECH is fine. INCOMPLETE THOUGHTS are not.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
USING CUT CONTEXT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Each clip may include CUT BEFORE and CUT AFTER lines — these show words that were removed from the video immediately before and after the clip. The viewer will NOT hear this removed content. Use it to judge:
- If CUT BEFORE ends mid-sentence and the clip continues that sentence → clip starts as a fragment
- If the clip ends mid-sentence and CUT AFTER completes it → clip has a dangling ending
- If a reference in the clip only makes sense with the cut context → contextless reference

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RESPONSE FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
For each problematic clip, one line:
  [clipIndex] REMOVE — reason
  [clipIndex] FLAG — reason

Use REMOVE for clearly broken clips (mid-sentence fragments, isolated filler, contextless references).
Use FLAG for borderline clips — a human editor should review but it might be fine.

If all clips are coherent: ALL_VALID"""


def build_user_message(clips: list[dict]) -> str:
    parts = []
    for c in clips:
        lines = [f"[{c['index']}] {c['text']}"]
        if c.get("before"):
            lines.append(f'  ↳ CUT BEFORE: "...{c["before"]}"')
        if c.get("after"):
            lines.append(f'  ↳ CUT AFTER: "{c["after"]}..."')
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


LINE_RE = re.compile(r"^\[(\d+)\]\s+(REMOVE|FLAG)\s*(?:—|-)?\s*(.*)$", re.IGNORECASE)


def parse_response(text: str) -> tuple[list[dict], list[dict]]:
    """Returns (remove_clips, flag_clips) each as [{index, reason}, ...]."""
    if text.strip() == "ALL_VALID":
        return [], []
    remove, flag = [], []
    for raw in text.splitlines():
        m = LINE_RE.match(raw.strip())
        if not m:
            continue
        idx = int(m.group(1))
        action = m.group(2).upper()
        reason = m.group(3).strip() or "(no reason given)"
        target = remove if action == "REMOVE" else flag
        target.append({"index": idx, "reason": reason})
    return remove, flag


def call_llm(user_message: str) -> str:
    """Try Anthropic first, fall back to Groq."""
    global MODEL
    if os.getenv("ANTHROPIC_API_KEY") and Anthropic is not None:
        MODEL = ANTHROPIC_MODEL
        client = Anthropic()
        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=2048,
            temperature=0,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_message}],
        )
        for block in resp.content:
            if block.type == "text":
                return block.text
        return ""
    if os.getenv("GROQ_API_KEY") and OpenAI is not None:
        MODEL = GROQ_MODEL
        client = OpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1",
        )
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            temperature=0,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        )
        return resp.choices[0].message.content or ""
    raise RuntimeError(
        "No LLM API key found. Set ANTHROPIC_API_KEY or GROQ_API_KEY in env."
    )


def chunk_clips(clips: list[dict]) -> list[list[dict]]:
    if len(clips) <= CHUNK_SIZE:
        return [clips]
    chunks = []
    step = CHUNK_SIZE - CHUNK_OVERLAP
    for i in range(0, len(clips), step):
        chunks.append(clips[i:i + CHUNK_SIZE])
        if i + CHUNK_SIZE >= len(clips):
            break
    return chunks


def dedupe(items: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for it in items:
        if it["index"] in seen:
            continue
        seen.add(it["index"])
        out.append(it)
    return out


def render_markdown(clips: list[dict], remove: list[dict], flag: list[dict]) -> str:
    lines = ["# Assembly coherence review", ""]
    if not remove and not flag:
        lines.append("All clips play cleanly as a sequence. No issues flagged.")
        return "\n".join(lines)

    if remove:
        lines.append("## REMOVE (recommended)")
        lines.append("")
        for r in remove:
            c = clips[r["index"]]
            label = c.get("label", f"#{r['index']}")
            lines.append(f"- **{label}** — {r['reason']}")
            lines.append(f"  > {c['text'][:200]}{'…' if len(c['text']) > 200 else ''}")
        lines.append("")
    if flag:
        lines.append("## FLAG (human review)")
        lines.append("")
        for f in flag:
            c = clips[f["index"]]
            label = c.get("label", f"#{f['index']}")
            lines.append(f"- **{label}** — {f['reason']}")
            lines.append(f"  > {c['text'][:200]}{'…' if len(c['text']) > 200 else ''}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("manifest", type=Path, help="Input manifest JSON with 'clips' array")
    ap.add_argument("--out-md", type=Path, required=True, help="Output markdown report")
    ap.add_argument("--out-json", type=Path, default=None, help="Optional JSON output")
    args = ap.parse_args()

    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("GROQ_API_KEY")):
        print("No LLM API key. Set ANTHROPIC_API_KEY or GROQ_API_KEY.", file=sys.stderr)
        return 2

    data = json.loads(args.manifest.read_text())
    clips = data.get("clips", [])
    if not clips:
        print("No clips in manifest.", file=sys.stderr)
        return 1
    for i, c in enumerate(clips):
        c["index"] = i

    all_remove: list[dict] = []
    all_flag: list[dict] = []
    for chunk in chunk_clips(clips):
        user_msg = build_user_message(chunk)
        print(f"  validating {len(chunk)} clips (indices {chunk[0]['index']}..{chunk[-1]['index']})...",
              file=sys.stderr)
        response = call_llm(user_msg)
        rem, fl = parse_response(response)
        all_remove.extend(rem)
        all_flag.extend(fl)

    remove = dedupe(all_remove)
    remove_idx = {r["index"] for r in remove}
    flag = dedupe([f for f in all_flag if f["index"] not in remove_idx])

    md = render_markdown(clips, remove, flag)
    args.out_md.write_text(md)
    print(f"Wrote {args.out_md}  ({len(remove)} REMOVE, {len(flag)} FLAG)")

    if args.out_json:
        args.out_json.write_text(json.dumps({
            "model": MODEL,
            "remove_clips": remove,
            "flag_clips": flag,
        }, indent=2))
        print(f"Wrote {args.out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
