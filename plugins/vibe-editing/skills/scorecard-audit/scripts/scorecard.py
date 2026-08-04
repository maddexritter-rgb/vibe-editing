#!/usr/bin/env python3
"""Scorecard audit — runs the company.com 51-rule scorecard against a clip's transcript.

Three format variants (auto-selected via --format or detected from clip metadata):
  - qa      → Q&A two-person podcast format (system.md, 151 max, 80% = 121 pass)
  - hotline → Creator Hotline rapid-fire multi-guest (121 max, 80% = 97 pass)
  - story   → Story-based single-narrator (max varies by variant)

Input  : transcript.json (our standard schema) + optional clip path
Output : <clip>.scorecard.json (their schema verbatim) + a human-readable .md report
         Also auto-translates each failed rule into tracker.py lesson entries so the
         precedent-retrieval loop picks them up on the next similar clip.

Schema (matches prompts/*.md exactly so we can interop with the upstream Electron app):
    {
      "total_score": int,
      "total_max": int,
      "pass": bool,
      "brand_advisory": [str],
      "categories": {<cat>: {"score": int, "max": int}, ...},
      "rules": {<id>: {"score": int|null, "max": int, "pass": bool, "flag": str|null, "timestamp": str|null}, ...}
    }

Auth: requires ANTHROPIC_API_KEY (same as correct_transcript.py). Falls back to
`claude -p` CLI if SDK + key are unavailable.
"""
from __future__ import annotations

# ── engine bundled-keys autoload (config/keys.env) ──
import os as _ko, pathlib as _kp
def _acq_load_keys():
    d = _kp.Path(__file__).resolve()
    for p in (d, *d.parents):
        if (p / ".claude-plugin").is_dir():
            f = p / "config" / "keys.env"
            if f.is_file():
                for _ln in f.read_text().splitlines():
                    _ln = _ln.strip()
                    if _ln and not _ln.startswith("#") and "=" in _ln:
                        _k, _v = _ln.split("=", 1); _k, _v = _k.strip(), _v.strip()
                        if _k and "PASTE" not in _v and not _ko.environ.get(_k):
                            _ko.environ[_k] = _v
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
from typing import Optional

SKILL_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = SKILL_ROOT / "prompts"
TRACKER = Path.home() / ".claude" / "skills" / "clip-review-tracker" / "scripts" / "tracker.py"

MODEL_DEFAULT = "claude-sonnet-4-5"

# Pass thresholds extracted from each prompt's RETURN FORMAT block
PASS_THRESHOLDS = {
    "qa":      {"max": 151, "pass": 121},  # 80%
    "hotline": {"max": 121, "pass": 97},
    "story":   {"max": 145, "pass": 116},  # ~80%, conservative; story.md may vary
}


def load_format_prompt(fmt: str) -> str:
    path = PROMPTS_DIR / f"{fmt}.md"
    if not path.exists():
        sys.exit(f"error: prompt not found at {path} — expected qa.md / hotline.md / story.md")
    return path.read_text()


def transcript_to_text(transcript: dict) -> str:
    """Convert our word-level transcript.json to MM:SS-formatted dialogue text."""
    words = transcript.get("words") or []
    if not words:
        return ""
    lines: list[str] = []
    # Group by 3-second windows; emit one line per window with a leading timestamp
    cur_start = words[0]["start"]
    cur_words: list[str] = []
    for w in words:
        if w["start"] - cur_start > 3.0 and cur_words:
            mm = int(cur_start // 60); ss = int(cur_start % 60)
            lines.append(f"[{mm:02d}:{ss:02d}] " + " ".join(cur_words))
            cur_start = w["start"]
            cur_words = []
        cur_words.append(w["word"])
    if cur_words:
        mm = int(cur_start // 60); ss = int(cur_start % 60)
        lines.append(f"[{mm:02d}:{ss:02d}] " + " ".join(cur_words))
    return "\n".join(lines)


def call_claude(system: str, user: str, model: str = MODEL_DEFAULT,
                max_tokens: int = 4096, timeout_sec: int = 180) -> str:
    """Run via Anthropic SDK if ANTHROPIC_API_KEY set; fall back to `claude -p`."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            import anthropic  # type: ignore
        except ImportError:
            raise RuntimeError("ANTHROPIC_API_KEY set but anthropic SDK missing — pip3 install --user anthropic")
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")

    # CLI fallback
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tf:
        tf.write(system + "\n\n---USER---\n\n" + user)
        prompt_path = tf.name
    try:
        result = subprocess.run(
            ["claude", "-p", f"@{prompt_path}"],
            capture_output=True, text=True, timeout=timeout_sec,
        )
        combined = ((result.stderr or "") + (result.stdout or ""))[:500]
        if result.returncode != 0 or "Not logged in" in combined:
            raise RuntimeError(
                "No Claude auth available. Set ANTHROPIC_API_KEY in shell OR run `claude /login` once.\n"
                f"CLI returned: {combined.strip()}"
            )
        return result.stdout
    finally:
        try:
            os.unlink(prompt_path)
        except OSError:
            pass


def parse_scorecard_json(raw: str) -> dict:
    """Extract the first JSON object from the LLM response."""
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"\s*```\s*$", "", raw, flags=re.MULTILINE)
    # Find the largest balanced JSON object
    depth = 0
    start = -1
    end = -1
    for i, c in enumerate(raw):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if start < 0 or end < 0:
        raise ValueError("no JSON object found in LLM response")
    return json.loads(raw[start:end])


def write_markdown_report(card: dict, fmt: str, out_path: Path) -> None:
    lines = [
        f"# Scorecard Audit — {fmt.upper()} format",
        "",
        f"**Total:** {card.get('total_score', 0)} / {card.get('total_max', '?')}  ",
        f"**Pass:** {'✅ YES' if card.get('pass') else '❌ NO'}  ",
        "",
    ]
    if card.get("brand_advisory"):
        lines.append("## Brand advisory")
        for a in card["brand_advisory"]:
            lines.append(f"- ⚠️ {a}")
        lines.append("")

    lines.append("## Category breakdown")
    lines.append("")
    lines.append("| Category | Score | Max |")
    lines.append("|----------|------:|----:|")
    for cat, v in (card.get("categories") or {}).items():
        lines.append(f"| {cat} | {v.get('score','?')} | {v.get('max','?')} |")
    lines.append("")

    lines.append("## Rule-by-rule")
    lines.append("")
    rules = card.get("rules") or {}
    failed = [(rid, r) for rid, r in rules.items() if r.get("pass") is False and r.get("flag") != "visual-check-required"]
    if failed:
        lines.append("### ❌ Failed rules (action required)")
        for rid, r in failed:
            ts = r.get("timestamp") or "—"
            lines.append(f"- **{rid}** ({r.get('score',0)}/{r.get('max',0)}): {r.get('flag','')} [{ts}]")
        lines.append("")
    visual = [rid for rid, r in rules.items() if r.get("flag") == "visual-check-required"]
    if visual:
        lines.append(f"### 👁️ Visual-check-required (handed off to sf-audit CV/vision checks)")
        lines.append(f"- {', '.join(visual)}")
        lines.append("")

    out_path.write_text("\n".join(lines))


def autolog_failures_as_lessons(card: dict, client: str, clip_slug: str,
                                 profile_hints: dict) -> int:
    """For each failed (non-visual) rule, call `tracker.py lesson` so the precedent
    retrieval loop sees the lesson next clip. Returns count of lessons recorded.

    WHEN clause is built from profile_hints so the lesson generalizes — never
    references the current clip slug.
    """
    if not TRACKER.exists():
        return 0
    rules = card.get("rules") or {}
    when_parts: list[str] = []
    for k, v in profile_hints.items():
        if v:
            when_parts.append(f"{k}={v}")
    when_clause = " AND ".join(when_parts) if when_parts else "client=" + client

    count = 0
    for rid, r in rules.items():
        if r.get("pass") is True or r.get("flag") == "visual-check-required":
            continue
        flag = (r.get("flag") or "").strip()
        if not flag or flag == "null":
            continue
        ts = r.get("timestamp") or "?"
        issue = f"Scorecard {rid} failed at {ts}: {flag}"
        # THEN clause is the rule's standard for next time — pull from id naming
        then_lookup = _rule_then(rid)
        if not then_lookup:
            continue
        try:
            subprocess.run(
                [sys.executable, str(TRACKER), "lesson", clip_slug,
                 "--client", client,
                 "--issue", issue,
                 "--when", when_clause,
                 "--then", then_lookup,
                 "--version", "scorecard-audit"],
                check=False, capture_output=True, text=True, timeout=20,
            )
            count += 1
        except (subprocess.SubprocessError, OSError):
            continue
    return count


# Canonical "what to do next time" mappings for each rule id. The audit only
# captures lessons for rules with a clean structural fix.
_RULE_THENS: dict[str, str] = {
    # Hook (Q&A)
    "H1": "Contrast hook: state [activity] BEFORE [revenue]. Lean into the mundanity — don't soften the activity. Example: 'I print stickers and I make $1.5M a year.'",
    "H2": "Speaker must speak or react within first 15s — no uninterrupted guest monologue past 15s mark",
    "H3": "Hook needs ONE of: <15s preface, immediate high-impact statement, or cold-open at peak tension before explanation",
    "H4": "No guest monologue >10s without Speaker reaction. Intersperse reactions every 8-10s",
    "H5": "Open with a visual title card / hook text overlay",
    # Pacing
    "P1": "No pause between cuts >0.5s. Run silence-snap (transcript-edit silence map) on all cuts",
    "P3": "Runtime under 90s. Target 60-75s. Cut to tighten if over.",
    "P4": "At least 2 hard cuts in first 15s — multi-angle hook",
    # Narrative (the big ones)
    "N1": "ONE problem, ONE solution. If two problems present, pick one and cut the other entirely.",
    "N2": "Guest's problem stated in plain language within first 30s — fix any buried/implied problem statement",
    "N3": "Speaker's solution direct + actionable + one sentence. Strip caveats around the solution.",
    "N4": "Payoff is the FINAL audible line. Last line MUST be the solution statement. Cut everything after the payoff lands.",
    "N5": "Remove any 10-30s tangent that doesn't change the solution",
    "N6": "Remove any adjacent-topic advice that splits viewer attention",
    "N7": "Video ends immediately after payoff — no 'so yeah, that's the thing' wrap-up",
    "N8": "First audible line is a complete coherent sentence — no greetings, no discourse markers ('So...', 'Well...'), no mid-thought",
    "N9": "Every utterance after a cut starts as a complete sentence (capital letter, standalone)",
    "N10": "Cut order doesn't change implied meaning — semantic continuity preserved across edits",
    # Subtitle (text-checkable)
    "S4": "All words lowercase except: proper nouns, I/I'm/I'd/I've/I'll",
    "S5": "Use $ symbol (not 'dollars'). Above $100K abbreviate: $250K, $1.2M, $3B",
    "S6": "No duplicate words in a single cue ('what's what's' → 'what's')",
    "S8": "Contractions: 'want to' → 'wanna', 'going to' → 'gonna'",
    "S9": "Parentheticals only when viewer lacks context — remove default-habit parens",
    "S10": "Long subtitles split at natural speech breaks. Explainer on its own line below",
    "S11": "Run client-vocab + dictionary spelling pass via correct_transcript.py before render",
    # Brand
    "B1": "Speaker speaks/reacts within first 15s",
    "B2": "Speaker authority maintained — no edit that frames Speaker as arrogant/dismissive without context",
    "B3": "Splitscreen 50/50 Speaker-top guest-bottom when both relevant; drop shadow on Speaker panel",
    "B4": "Show guest on camera during their speaking/reacting moments — never hide guest while Speaker explains",
    # Editing discipline
    "E1": "Remove all fillers: um/uh/like/so/you know/I mean. Use detect_fillers.py + 200ms pad",
    "E2": "Remove segments under 2s with no meaningful speech (crosstalk, noise, pre-conversation chatter)",
    "E3": "After any trim, drop floating single-token or discourse-marker leftovers (So./Yeah./Right.)",
    "E4": "When 2+ examples make the same point, keep only the strongest and most universally relatable",
    "E5": "Same-speaker merges must read as one coherent sentence — no semantic contradictions",
    # Brand risk
    "R1": "Remove any 'my price is $X' specific-pricing mentions. Revenue/profit OK.",
    "R2": "Remove any 'I fired X' / 'we let X go' about identifiable individuals",
    "R3": "No curse words or trigger words in first 15s",
    "R4": "All music royalty-free / cleared",
    # Hotline
    "HH2": "Each guest question <6s — compress any longer",
    "HH4": "No single question+answer exchange >15s",
    "HN2": "Each Speaker response ≤8s with one clear takeaway",
}


def _rule_then(rid: str) -> str:
    return _RULE_THENS.get(rid, "")


def detect_format(transcript: dict, hint: Optional[str] = None) -> str:
    """If --format not given, infer from clip duration + speaker turn density.
    Q&A is the default; switch to hotline if many guest-Speaker turns in a short clip."""
    if hint:
        return hint
    words = transcript.get("words") or []
    if not words:
        return "qa"
    duration = words[-1]["end"] - words[0]["start"]
    if duration < 90 and len(words) > 200:
        # Dense rapid-fire — likely hotline. Crude but better than always defaulting.
        return "hotline"
    return "qa"


def main() -> int:
    ap = argparse.ArgumentParser(description="company.com 51-rule scorecard audit")
    ap.add_argument("--transcript", required=True, type=Path,
                    help="transcript.json (our schema — words + start/end)")
    ap.add_argument("--clip", type=Path,
                    help="Optional clip path for context + report co-location")
    ap.add_argument("--format", choices=["qa", "hotline", "story"], default=None,
                    help="Format variant. If omitted, auto-detected from transcript.")
    ap.add_argument("--out", type=Path,
                    help="Output .scorecard.json path. Default: alongside --clip or --transcript.")
    ap.add_argument("--out-md", type=Path,
                    help="Markdown report path. Default: alongside .scorecard.json.")
    ap.add_argument("--model", default=MODEL_DEFAULT)
    # Profile / tracker integration
    ap.add_argument("--client", help="Brand slug — required for autolog-lessons")
    ap.add_argument("--clip-slug", help="Clip slug for tracker.py lesson autolog")
    ap.add_argument("--clip-type", help="q&a / monologue / etc — flows into WHEN clause")
    ap.add_argument("--gesture-profile", help="For WHEN clause")
    ap.add_argument("--caption-style", help="For WHEN clause")
    ap.add_argument("--hook-type", help="For WHEN clause")
    ap.add_argument("--no-autolog", action="store_true",
                    help="Skip auto-logging failed rules as tracker.py lessons")
    # Behavior
    ap.add_argument("--exit-on-fail", action="store_true",
                    help="Exit non-zero if total_score < pass threshold (use as a ship gate)")
    args = ap.parse_args()

    if not args.transcript.exists():
        sys.exit(f"error: transcript not found at {args.transcript}")

    transcript = json.loads(args.transcript.read_text())
    fmt = detect_format(transcript, args.format)
    if fmt != args.format and args.format is None:
        print(f"  auto-detected format: {fmt}", file=sys.stderr)

    system_prompt = load_format_prompt(fmt)
    text = transcript_to_text(transcript)
    if not text:
        sys.exit("error: transcript has no words to score")

    duration = (transcript.get("words") or [{}])[-1].get("end", 0)
    user_msg = (
        f"Transcript (with MM:SS timestamps):\n\n{text}\n\n"
        f"Clip duration: {duration:.1f}s\n"
        f"Format: {fmt}\n\n"
        "Return the scorecard JSON exactly per the schema above. No prose."
    )

    print(f"Scoring {args.transcript.name} as {fmt.upper()} ({duration:.1f}s)...", flush=True)
    raw = call_claude(system_prompt, user_msg, model=args.model)
    try:
        card = parse_scorecard_json(raw)
    except (ValueError, json.JSONDecodeError) as e:
        sys.exit(f"error: failed to parse LLM response: {e}\nFirst 500 chars: {raw[:500]}")

    # Resolve output paths
    base = args.clip or args.transcript
    out_json = args.out or base.with_suffix(".scorecard.json")
    out_md = args.out_md or out_json.with_suffix(".md")
    out_json.write_text(json.dumps(card, indent=2, ensure_ascii=False))
    write_markdown_report(card, fmt, out_md)
    print(f"  total: {card.get('total_score')}/{card.get('total_max')} "
          f"{'✅ PASS' if card.get('pass') else '❌ FAIL'}")
    print(f"  wrote {out_json}")
    print(f"  wrote {out_md}")

    # Auto-log failures
    if not args.no_autolog and args.client and args.clip_slug:
        profile_hints = {
            "clip_type": args.clip_type or "",
            "gesture_profile": args.gesture_profile or "",
            "caption_style": args.caption_style or "",
            "hook_type": args.hook_type or "",
        }
        n = autolog_failures_as_lessons(card, args.client, args.clip_slug, profile_hints)
        if n:
            print(f"  → logged {n} scorecard failure(s) as tracker.py lessons")

    if args.exit_on_fail and not card.get("pass"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
