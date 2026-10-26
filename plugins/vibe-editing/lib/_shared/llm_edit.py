#!/usr/bin/env python3
"""LLM-driven editorial cuts for a clip range.

Ports CLIPPER-3.0's HOOK -> MEAT -> PAYOFF prompt (src/prompts/default-edit.ts).
Groups word-level transcript into utterances by pause, asks Claude for
per-utterance KEEP / REMOVE / TRIM decisions, then maps those decisions back
to word timestamps and emits a cuts JSON compatible with cut_clip.py
(same schema as detect_fillers.py output).

Complements detect_fillers.py — filler detection handles single-word/phrase
cuts with regex precision; this handles content-level cuts (tangents, repeat
examples, rambles) that only an LLM can judge.

Usage:
    python3 llm_edit.py transcript.json \
        --start 311.03 --end 340.00 \
        --out /tmp/clip-A-llm-cuts.json

Requires: ANTHROPIC_API_KEY in env.
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
    Anthropic = None  # fall back to Groq

try:
    from openai import OpenAI  # Groq uses OpenAI-compatible API
except ImportError:
    OpenAI = None


ANTHROPIC_MODEL = "claude-sonnet-4-6"
GROQ_MODEL = "llama-3.3-70b-versatile"
MODEL = ANTHROPIC_MODEL  # overridden at runtime based on which API key is set

# HOOK -> MEAT -> PAYOFF prompt ported from CLIPPER-3.0 src/prompts/default-edit.ts.
# System-prompt content is cacheable — use cache_control on the last block.
SYSTEM_PROMPT = """## ROLE
You are a short-form content editor. You extract and tighten the strongest moments from a raw transcript into a clean, compelling clip.

## TASK
Given a raw transcript with numbered utterances, produce per-utterance editing decisions that follow a HOOK -> MEAT -> PAYOFF arc.

You may only KEEP, REMOVE, or TRIM existing text — never add new words, fabricate, or rearrange the order of the transcript.

## WORD BUDGET
Aim for 80-180 words of kept/trimmed content in the final output (30-60 second clip).

## PROCESS

### Phase 1: Build the HOOK
The hook is the punchline, not the lead-in. Scan the first 10 words — if they're setup/context ("And so...", "Let's say...", "I was thinking..."), TRIM to start from the first declarative statement. Strong hooks convey:
- Stakes (why this matters)
- Contrast or tension (a surprising number, contrarian framing, clear problem)
- Specificity (concrete numbers, names, claims — not vague)

### Phase 2: Identify the PAYOFF
The payoff is the key insight, reframe, or concrete action that resolves the tension. The clip ends ON the payoff — nothing trails. The payoff must:
- Be a concrete statement, not vague advice
- Be understandable to a viewer who only saw the hook
- Land on a sentence terminator (. ! ?), never mid-clause

### Phase 3: Connect the MEAT
The meat is everything between hook and payoff. Keep the lines that build toward the insight — analogies, frameworks, diagnostic exchanges if they make the payoff land. Cut tangents, repeat examples, and side stories that make the same point twice.

### Phase 4: Final read-through
Read only the KEEP and TRIM lines in sequence as a first-time viewer would hear them. Check:
- Every sentence is grammatically complete
- No line starts or ends mid-thought
- No jarring jumps between kept lines
- Clip opens cleanly and ends on the payoff

## EDITING RULES
- **FILLER RULE:** Never KEEP a line whose entire content is filler or discourse markers (Okay, Yeah, Mhmm, Right, So, Now, Perfect, Alright, Great, Sure, Yep, Cool, Absolutely, Wow, Uh, Um, Oh). Always REMOVE. If a filler word opens a substantive sentence, TRIM to start from the substantive part.
- **FRAGMENT RULE:** If an utterance is entirely incoherent fragments, crosstalk, or noise, REMOVE it. After trimming, if an utterance would be left as a fragment that cannot stand alone — REMOVE it instead.
- Remove filler words, false starts, and stutters within kept lines.
- Tighten sentences — keep them punchy while preserving the speaker's voice.
- When multiple examples illustrate the same point, keep only the strongest one.

## OUTPUT FORMAT
For each utterance in the input transcript, output exactly one decision line:

`[index] KEEP` — use original utterance text as-is
`[index] REMOVE` — cut this utterance entirely
`[index] TRIM: <trimmed text>` — replace with trimmed version (must use ONLY words from the original)

Rules:
- One decision per line, in index order
- Every input index MUST have a decision (no gaps)
- TRIM text uses ONLY original words (no new words, no rearrangement)
- No commentary, no headers — ONLY decision lines

Now output ONLY decision lines for the following transcript:"""


# Pause threshold for splitting words into utterances when diarization is absent.
UTTERANCE_PAUSE_SEC = 0.8


def group_words_into_utterances(words: list[dict]) -> list[dict]:
    """Group word-level transcript into utterances on long pauses.

    Each utterance: {index, start, end, text, words: [...], speaker?}
    If words have a `speaker` field, a speaker change also starts a new utterance.
    """
    if not words:
        return []

    utterances: list[dict] = []
    current: list[dict] = [words[0]]

    for i in range(1, len(words)):
        prev = words[i - 1]
        w = words[i]
        gap = w["start"] - prev["end"]
        speaker_changed = (
            "speaker" in w and "speaker" in prev and w["speaker"] != prev["speaker"]
        )
        if gap >= UTTERANCE_PAUSE_SEC or speaker_changed:
            utterances.append(_build_utterance(current, len(utterances)))
            current = [w]
        else:
            current.append(w)
    if current:
        utterances.append(_build_utterance(current, len(utterances)))
    return utterances


def _build_utterance(words: list[dict], index: int) -> dict:
    text = " ".join(w["word"].strip() for w in words).strip()
    return {
        "index": index,
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "text": text,
        "words": words,
        "speaker": words[0].get("speaker"),
    }


def build_user_message(utterances: list[dict]) -> str:
    lines = []
    for u in utterances:
        label = f"Speaker {u['speaker']}" if u.get("speaker") is not None else "Speaker"
        lines.append(f"[{u['index']}] {label}: {u['text']}")
    return "## Transcript\n" + "\n".join(lines)


DECISION_RE = re.compile(r"^\[(\d+)\]\s+(KEEP|REMOVE|TRIM)(?:\s*:\s*(.*))?$", re.IGNORECASE)


def parse_decisions(response: str, total: int) -> list[dict]:
    """Parse decisions. Missing indices default to KEEP (safe — never silently cut)."""
    decisions: dict[int, dict] = {}
    cleaned = response.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[^\n]*\n?", "", cleaned).rstrip("`\n")
    for raw in cleaned.splitlines():
        m = DECISION_RE.match(raw.strip())
        if not m:
            continue
        idx = int(m.group(1))
        action = m.group(2).lower()
        text = (m.group(3) or "").strip() or None
        if action == "trim" and not text:
            decisions[idx] = {"index": idx, "action": "keep"}
        else:
            decisions[idx] = {"index": idx, "action": action, "text": text}

    out = []
    missing = []
    for i in range(total):
        if i in decisions:
            out.append(decisions[i])
        else:
            missing.append(i)
            out.append({"index": i, "action": "keep"})
    if missing:
        print(f"  [warn] {len(missing)} utterances missing from LLM response, defaulted to KEEP: {missing}",
              file=sys.stderr)
    return out


_WORD_NORM = re.compile(r"[^\w']", re.UNICODE)


def _norm_tok(s: str) -> str:
    return _WORD_NORM.sub("", s.strip().lower())


def map_trim_to_kept_words(utterance: dict, trimmed_text: str) -> list[bool] | None:
    """Greedy forward-scan: match trimmed_text tokens against utterance words.

    Returns a boolean mask (True = keep) the same length as utterance['words'],
    or None if <60% of trimmed tokens matched (falls back to full KEEP).
    """
    src_words = utterance["words"]
    trim_toks = [_norm_tok(t) for t in trimmed_text.split() if _norm_tok(t)]
    if not trim_toks:
        return [False] * len(src_words)

    mask = [False] * len(src_words)
    ti = 0
    for si, sw in enumerate(src_words):
        if ti >= len(trim_toks):
            break
        if _norm_tok(sw["word"]) == trim_toks[ti]:
            mask[si] = True
            ti += 1

    matched_ratio = ti / len(trim_toks)
    if matched_ratio < 0.6:
        return None
    return mask


def decisions_to_cuts(utterances: list[dict], decisions: list[dict]) -> list[dict]:
    """Convert per-utterance decisions into a cuts list (time ranges to REMOVE).

    Output schema matches detect_fillers.py — compatible with cut_clip.py.
    """
    cuts: list[dict] = []
    for u, d in zip(utterances, decisions):
        action = d["action"]
        if action == "keep":
            continue
        if action == "remove":
            prev_end = u["words"][0]["start"] - 0.1
            next_start = u["words"][-1]["end"] + 0.1
            cuts.append({
                "start": round(u["start"], 3),
                "end": round(u["end"], 3),
                "match": f"[llm-remove] utterance {u['index']}",
                "_prev_end": round(prev_end, 3),
                "_next_start": round(next_start, 3),
            })
            continue
        if action == "trim":
            trimmed_text = d.get("text") or ""
            mask = map_trim_to_kept_words(u, trimmed_text)
            if mask is None:
                print(f"  [warn] TRIM match <60% on utterance {u['index']}; keeping full utterance",
                      file=sys.stderr)
                continue
            # Emit a cut for each maximal run of False in the mask.
            run_start = None
            for i, keep in enumerate(mask):
                if not keep and run_start is None:
                    run_start = i
                elif keep and run_start is not None:
                    _emit_run_cut(u["words"], run_start, i - 1, u["index"], cuts)
                    run_start = None
            if run_start is not None:
                _emit_run_cut(u["words"], run_start, len(mask) - 1, u["index"], cuts)
    cuts.sort(key=lambda c: c["start"])
    return cuts


def _emit_run_cut(words: list[dict], i0: int, i1: int, utt_idx: int, cuts: list[dict]) -> None:
    first = words[i0]
    last = words[i1]
    prev_end = words[i0 - 1]["end"] if i0 > 0 else first["start"] - 0.1
    next_start = words[i1 + 1]["start"] if i1 + 1 < len(words) else last["end"] + 0.1
    cuts.append({
        "start": round(first["start"], 3),
        "end": round(last["end"], 3),
        "match": f"[llm-trim] utt {utt_idx} words {i0}-{i1}",
        "_prev_end": round(prev_end, 3),
        "_next_start": round(next_start, 3),
    })


def call_llm(user_message: str) -> str:
    """Try Anthropic first, fall back to Groq. Returns the model's text response."""
    global MODEL
    if os.getenv("ANTHROPIC_API_KEY") and Anthropic is not None:
        MODEL = ANTHROPIC_MODEL
        client = Anthropic()
        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=4096,
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
            max_tokens=4096,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        )
        return resp.choices[0].message.content or ""
    raise RuntimeError(
        "No LLM API key found. Set ANTHROPIC_API_KEY or GROQ_API_KEY in env."
    )


def slice_words(words: list[dict], t0: float, t1: float) -> list[dict]:
    return [w for w in words if w["end"] > t0 and w["start"] < t1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("transcript", type=Path, help="Word-level transcript JSON")
    ap.add_argument("--start", type=float, required=True, help="Clip start time (sec)")
    ap.add_argument("--end", type=float, required=True, help="Clip end time (sec)")
    ap.add_argument("--out", type=Path, required=True, help="Output cuts JSON")
    ap.add_argument("--dry-run", action="store_true", help="Print LLM response, don't write cuts")
    ap.add_argument("--min-keep-ratio", type=float, default=0.60,
                    help="If LLM cuts leave <this fraction of source duration kept, abort with "
                         "exit 4 (caller should fall back to detect_fillers.py). Default 0.60.")
    args = ap.parse_args()

    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("GROQ_API_KEY")):
        print("No LLM API key. Set ANTHROPIC_API_KEY or GROQ_API_KEY.", file=sys.stderr)
        return 2

    tr = json.loads(args.transcript.read_text())
    words = slice_words(tr.get("words", []), args.start, args.end)
    if not words:
        print(f"No words in range [{args.start}, {args.end}]", file=sys.stderr)
        return 1

    utterances = group_words_into_utterances(words)
    user_msg = build_user_message(utterances)
    print(f"  {len(utterances)} utterances, calling LLM...", file=sys.stderr)

    response = call_llm(user_msg)
    print(f"  using model: {MODEL}", file=sys.stderr)
    if args.dry_run:
        print(response)
        return 0

    decisions = parse_decisions(response, len(utterances))
    cuts = decisions_to_cuts(utterances, decisions)

    # Guardrail: refuse to emit cuts that would over-compress a long narrative.
    # Groq (llama-3.3-70b) in particular misreads long reveal-stories as ramble
    # and trims a 150s monologue down to 6-8s. If <min_keep_ratio of the source
    # duration would remain, abort so the caller can fall back to detect_fillers.
    source_dur = max(args.end - args.start, 0.01)
    removed_dur = sum(c["end"] - c["start"] for c in cuts)
    keep_ratio = 1.0 - (removed_dur / source_dur)
    if keep_ratio < args.min_keep_ratio:
        print(f"  [abort] LLM would keep only {keep_ratio*100:.0f}% of {source_dur:.1f}s source "
              f"(< {args.min_keep_ratio*100:.0f}% threshold). Over-compression detected — "
              f"falling back required. Use detect_fillers.py or narrow the source window.",
              file=sys.stderr)
        return 4

    payload = {
        "transcript": str(args.transcript),
        "scope": {"start": args.start, "end": args.end},
        "source": "llm_edit",
        "model": MODEL,
        "decisions": decisions,
        "cuts": cuts,
        "keep_ratio": round(keep_ratio, 3),
    }
    args.out.write_text(json.dumps(payload, indent=2))
    removed = sum(1 for d in decisions if d["action"] == "remove")
    trimmed = sum(1 for d in decisions if d["action"] == "trim")
    print(f"Wrote {args.out}  ({len(cuts)} cuts — {removed} REMOVE, {trimmed} TRIM, "
          f"{keep_ratio*100:.0f}% kept)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
