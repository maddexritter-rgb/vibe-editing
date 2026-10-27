#!/usr/bin/env python3
"""audit-script: independent editorial/script quality gate.

Transcribes the clip independently, then runs editorial checks:
cold viewer test, context-before-payoff, one-arc rule, hook quality,
payoff resolution, logical flow, brand safety, and length.

The heavy editorial checks (cold viewer, hook, flow) are best run by an
LLM sub-agent reading the transcript. This script handles the mechanical
checks (length, brand safety keywords) and prepares the transcript +
structured prompt for the LLM pass.
"""

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
import subprocess
import sys
import tempfile
from pathlib import Path

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
WORDS_PER_SECOND = 3.5  # average speech rate


def transcribe_clip(clip_path: str) -> dict:
    """Transcribe clip audio — Groq if a key is set, else local whisper (key-free)."""
    if not GROQ_API_KEY:
        import json as _json, sys as _sys
        from pathlib import Path as _Path
        _r = _Path(__file__).resolve()
        _root = next((p for p in (_r, *_r.parents) if (p / ".claude-plugin").is_dir()), _r.parents[3])
        _tl = _root / "skills" / "long-form-ingest" / "scripts" / "transcribe_local.py"
        _out = tempfile.mktemp(suffix=".json")
        _p = subprocess.run([_sys.executable, str(_tl), clip_path, "--out", _out],
                            capture_output=True, text=True)
        if _p.returncode != 0 or not os.path.exists(_out):
            return {"text": "", "words": [], "segments": [],
                    "_note": "transcription unavailable (no Groq key + local whisper failed)"}
        _d = _json.load(open(_out)); os.unlink(_out)
        return {"text": _d.get("text", ""), "words": _d.get("words", []), "segments": []}
    wav = tempfile.mktemp(suffix=".wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", clip_path, "-ac", "1", "-ar", "16000", wav],
        capture_output=True,
    )
    try:
        import httpx
        with open(wav, "rb") as f:
            resp = httpx.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files={"file": ("audio.wav", f, "audio/wav")},
                data={
                    "model": "whisper-large-v3",
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "segment",
                },
                timeout=120,
            )
        resp.raise_for_status()
        return resp.json()
    finally:
        os.unlink(wav) if os.path.exists(wav) else None


def get_duration(clip_path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", clip_path],
        capture_output=True, text=True,
    )
    return float(json.loads(r.stdout).get("format", {}).get("duration", 0))


def check_length(word_count: int, duration: float) -> dict:
    """Check clip length against category thresholds."""
    speech_duration = word_count / WORDS_PER_SECOND
    issues = []

    if speech_duration <= 40:
        category = "short"
    elif speech_duration <= 75:
        category = "mid"
    else:
        category = "long"

    if speech_duration > 90:
        issues.append({
            "severity": "error",
            "problem": f"speech duration ~{round(speech_duration)}s exceeds 90s hard cap",
        })
    elif speech_duration > 75 and category != "mid":
        issues.append({
            "severity": "warn",
            "problem": f"speech duration ~{round(speech_duration)}s — approaching 90s cap",
        })

    has_error = any(i["severity"] == "error" for i in issues)
    return {
        "pass": not has_error,
        "category": category,
        "speech_duration_s": round(speech_duration),
        "word_count": word_count,
        "issues": issues,
    }


def check_brand_safety(text: str, vocab_path: str | None = None) -> dict:
    """Check for brand safety violations using keyword matching."""
    issues = []
    text_lower = text.lower()

    # Profanity check (basic)
    profanity = ["fuck", "shit", "damn", "ass ", "bitch", "hell "]
    for word in profanity:
        if word in text_lower:
            # Find apmontserratte position
            idx = text_lower.index(word)
            context = text[max(0, idx - 20):idx + 30]
            issues.append({
                "severity": "warn",
                "word": word.strip(),
                "context": context,
                "problem": f"profanity: '{word.strip()}'",
            })

    # Specific dollar amounts (often brand-restricted)
    dollar_matches = re.findall(r'\$[\d,]+(?:\.\d+)?', text)
    for dm in dollar_matches:
        # Clean and check magnitude
        amount_str = dm.replace("$", "").replace(",", "")
        try:
            amount = float(amount_str)
            if amount >= 100:  # Small amounts usually fine
                issues.append({
                    "severity": "warn",
                    "amount": dm,
                    "problem": f"specific dollar amount: {dm}",
                })
        except ValueError:
            pass

    # Load custom vocab if available
    if vocab_path and os.path.exists(vocab_path):
        with open(vocab_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or not line:
                    continue
                if "BLOCK:" in line:
                    blocked = line.split("BLOCK:")[1].strip().lower()
                    if blocked in text_lower:
                        issues.append({
                            "severity": "error",
                            "problem": f"blocked term: '{blocked}'",
                        })

    has_error = any(i["severity"] == "error" for i in issues)
    return {"pass": not has_error, "issues": issues[:10]}


def check_opener(text: str) -> dict:
    """Check if the opener is clean (no filler words, no mid-conversation start)."""
    issues = []
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return {"pass": True, "issues": []}

    first_line = lines[0].lower()

    # Filler openers
    fillers = ["yeah", "so", "um", "uh", "well", "okay so", "right so", "and", "but"]
    for filler in fillers:
        if first_line.startswith(filler + " ") or first_line == filler:
            issues.append({
                "severity": "warn",
                "problem": f"opens with filler: '{filler}'",
                "first_words": first_line[:50],
            })
            break

    # Mid-conversation start
    continuations = [
        "that's why", "that's what", "so basically", "the point is",
        "and that's", "which is why", "because of that",
    ]
    for cont in continuations:
        if first_line.startswith(cont):
            issues.append({
                "severity": "error",
                "problem": f"starts mid-conversation: '{cont}...'",
                "first_words": first_line[:60],
            })
            break

    has_error = any(i["severity"] == "error" for i in issues)
    return {"pass": not has_error, "issues": issues}


def check_dangling_references(text: str) -> dict:
    """Basic check for dangling references that need context not provided."""
    issues = []
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # Common dangling patterns
    danglers = [
        (r"\bthat (?:thing|focus|approach|method|strategy)\b", "dangling 'that [noun]'"),
        (r"\bwhat (?:he|she|they) (?:said|mentioned|talked about)\b", "references prior speech"),
        (r"\bthe same (?:thing|way|approach)\b", "dangling 'the same [noun]'"),
        (r"\blike I (?:said|mentioned)\b", "self-reference to earlier speech"),
        (r"\bas I was saying\b", "mid-conversation continuation"),
    ]

    for i, line in enumerate(lines[:5]):  # Focus on early lines
        line_lower = line.lower()
        for pattern, desc in danglers:
            if re.search(pattern, line_lower):
                # Only flag if it's in the first 3 lines (later is usually fine)
                if i < 3:
                    issues.append({
                        "line": i + 1,
                        "text": line[:60],
                        "problem": desc,
                        "severity": "warn",
                    })

    return {"pass": True, "issues": issues}  # These are warnings — the LLM pass is authoritative


def prepare_llm_audit_prompt(transcript_text: str, checks_so_far: dict) -> str:
    """Prepare the structured prompt for the LLM editorial audit.

    This prompt is meant to be given to a Claude sub-agent to perform
    the subjective editorial checks (cold viewer, hook, flow, etc.).
    """
    return f"""You are an editorial auditor for short-form video clips. You receive ONLY the transcript of a rendered clip. You have ZERO context about what footage it came from, who edited it, or what the full conversation was about.

Read this transcript as if you just opened this video cold. You don't know the speaker. You don't know the show. You are a random person scrolling.

TRANSCRIPT:
---
{transcript_text}
---

Check EACH of the following and return a JSON object:

1. **cold_viewer** — Does every line make complete sense given ONLY the prior lines in this clip? Flag dangling references ("that thing", "what he said"), undefined jargon, mid-conversation continuations.

2. **context_payoff** — If this is Q&A: does the question appear before the answer? If advice: is the problem stated before the solution? If a story: is there setup before punchline?

3. **one_arc** — Does the clip cover exactly ONE topic/story/arc? Flag any topic changes or two separate Q&A pairs stitched together.

4. **hook_quality** — Would the first sentence make a stranger stop scrolling? Classify the hook type: contrarian / story / stat / confession / stakes / question / list / reveal / generic. Flag if generic or requires insider knowledge.

5. **payoff_lands** — Does the ending resolve the setup? Flag trails-off, introduces-new-idea, ends-on-question-without-answer.

6. **logical_flow** — Between each sentence, does it follow from the previous one? Flag missing connecting logic, pronouns without antecedent, cause-effect where cause was cut.

Return ONLY this JSON (no markdown, no explanation):
{{
  "cold_viewer": {{"pass": bool, "issues": []}},
  "context_payoff": {{"pass": bool, "issues": []}},
  "one_arc": {{"pass": bool, "issues": []}},
  "hook_quality": {{"pass": bool, "hook_type": "...", "hook_text": "first sentence", "issues": []}},
  "payoff_lands": {{"pass": bool, "issues": []}},
  "logical_flow": {{"pass": bool, "issues": []}}
}}

Each issue should have: {{"line": N, "text": "quoted text", "problem": "description", "severity": "error"|"warn"}}
Be strict. If in doubt, flag it as a warn."""


def main():
    parser = argparse.ArgumentParser(description="Audit editorial quality on a rendered clip")
    parser.add_argument("--clip", required=True, help="Path to rendered clip mp4")
    parser.add_argument("--out", required=True, help="Output JSON path")
    parser.add_argument("--vocab", help="Path to brand vocab.txt for brand safety")
    parser.add_argument("--llm-audit", action="store_true", help="Print LLM audit prompt to stdout for sub-agent")
    args = parser.parse_args()

    clip = args.clip
    if not os.path.exists(clip):
        print(f"ERROR: clip not found: {clip}", file=sys.stderr)
        sys.exit(1)

    duration = get_duration(clip)

    # Transcribe independently
    print("Transcribing clip independently...")
    transcript = transcribe_clip(clip)
    full_text = transcript.get("text", "")

    if not full_text and "segments" in transcript:
        full_text = " ".join(s.get("text", "") for s in transcript["segments"])

    word_count = len(full_text.split())
    print(f"Transcribed: {word_count} words, ~{round(duration, 1)}s")

    results = {
        "clip": os.path.basename(clip),
        "transcript_word_count": word_count,
        "estimated_duration_s": round(duration, 1),
    }
    checks = {}

    # Mechanical checks (no LLM needed)
    print("Checking length...")
    checks["length"] = check_length(word_count, duration)

    print("Checking brand safety...")
    checks["brand_safety"] = check_brand_safety(full_text, args.vocab)

    print("Checking opener...")
    checks["opener"] = check_opener(full_text)

    print("Checking for dangling references...")
    checks["dangling_references"] = check_dangling_references(full_text)

    # LLM editorial checks — prepare prompt
    # These checks (cold_viewer, context_payoff, one_arc, hook_quality, payoff_lands, logical_flow)
    # require an LLM to evaluate. The prompt is either printed for a sub-agent or
    # placeholders are left in the output.
    llm_prompt = prepare_llm_audit_prompt(full_text, checks)

    if args.llm_audit:
        # Print the prompt for a sub-agent to process
        print("\n=== LLM AUDIT PROMPT ===")
        print(llm_prompt)
        print("=== END PROMPT ===\n")

    # Add placeholder LLM checks
    for check_name in ["cold_viewer", "context_payoff", "one_arc", "hook_quality", "payoff_lands", "logical_flow"]:
        checks[check_name] = {"pass": True, "issues": [], "note": "requires LLM sub-agent — run with --llm-audit"}

    # Verdict (based on mechanical checks only — LLM checks override when run)
    any_fail = any(not c["pass"] for c in checks.values())
    results["verdict"] = "FAIL" if any_fail else "PASS"
    results["checks"] = checks
    results["transcript"] = full_text
    results["llm_audit_prompt"] = llm_prompt

    failures = [k for k, v in checks.items() if not v["pass"]]
    if failures:
        first_fail = failures[0]
        first_issue = checks[first_fail]["issues"][0] if checks[first_fail]["issues"] else {}
        results["summary"] = f"FAIL: {', '.join(failures)}. {first_issue.get('problem', 'see details')}"
    else:
        results["summary"] = "Mechanical checks passed — LLM editorial audit recommended"

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n{results['verdict']}: {results['summary']}")
    print(f"Report: {args.out}")


if __name__ == "__main__":
    main()
