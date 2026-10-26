#!/usr/bin/env python3
"""tam_tighten — HOOK->MEAT->PAYOFF within-clip tightener (optional runner).

Ported from Julian's CLIPPER_X (src/prompts/default-edit.ts editorial logic + the
submit_edit_decisions tool schema in src/lib/llm.ts). Runs prompts/tam_tighten.md over ONE exchange
and turns the model's KEEP/REMOVE/TRIM decisions into cut-intervals (the time ranges to DROP) that
cut_clip.py --fillers can consume directly — same role as detect_fillers / llm_edit, mergeable union.

What was ported / changed:
  • Editorial logic kept: lead on the caller's problem+numbers, never open on the host, protect
    revenue/lead/price numbers, kill filler & false starts, keep the strongest example only.
  • DELETED (per our pipeline): the 300–400 word / ~60s budget and the "cut everything after the last
    payoff" cap. Our clips are content-driven 25–150s. (Both deletions live in tam_tighten.md.)
  • submit_edit_decisions tool: index + action(KEEP|REMOVE|TRIM) + trimmed_text, with the same
    validation as CLIPPER_X's parseIndexedDecisions (every index decided, no dupes, TRIM needs text;
    missing indices default to KEEP).

Decisions -> cuts: this is a WORD-LEVEL tightener, so it needs per-utterance word timing. Input must
be a transcript with `segments` (or `utterances`) each carrying `words:[{word,start,end}]`. For:
  • REMOVE  -> drop the whole utterance's [start,end].
  • TRIM    -> keep only the contiguous run of original words that appears in trimmed_text; drop the
               leading/trailing words outside that run (start-trim and end-trim intervals).
  • KEEP    -> drop nothing.
Output: {"cuts":[{start,end,reason}], "kept_text": "..."} in SOURCE seconds.

Reuses tam_select.py's Claude-call pattern (SDK -> claude CLI). The SDK path uses the tool; the CLI
fallback parses "[i] KEEP/REMOVE/TRIM: ..." decision lines (the prompt emits those when no tool).

Usage:
  python3 tam_tighten.py --transcript exchange_words.json --format hotline --out tighten/A.cuts.json
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
import argparse, json, os, re, subprocess, sys
from pathlib import Path

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "tam_tighten.md"
SHORT_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "tam_tighten_short.md"

EDIT_TOOL = {
    "name": "submit_edit_decisions",
    "description": "Submit per-utterance KEEP/REMOVE/TRIM editing decisions for the exchange.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "description": "the utterance index"},
                        "action": {"type": "string", "enum": ["KEEP", "REMOVE", "TRIM"]},
                        "trimmed_text": {
                            "type": "string",
                            "description": "required for TRIM; uses ONLY words from the original utterance",
                        },
                    },
                    "required": ["index", "action"],
                },
            }
        },
        "required": ["decisions"],
    },
}

PUNCT_RE = re.compile(r"[^\w']", flags=re.UNICODE)


def norm(w: str) -> str:
    return PUNCT_RE.sub("", str(w).strip().lower())


# ---- Claude call (key from env/~/.zshrc; SDK with tool, else claude CLI) ----------------------

def get_key() -> str | None:
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k:
        return k
    try:
        m = re.search(r"sk-ant-[A-Za-z0-9_\-]+", (Path.home() / ".zshrc").read_text())
        if m:
            return m.group(0)
    except Exception:
        pass
    return None


def call_decisions(system: str, user: str, n_indices: int) -> dict[int, dict]:
    """Return {index: {'action':..., 'trimmed_text':...}}. SDK tool path first, CLI line-parse fallback."""
    key = get_key()
    if key:
        try:
            import anthropic
            c = anthropic.Anthropic(api_key=key)
            r = c.messages.create(
                model="claude-sonnet-4-6", max_tokens=8192, temperature=0,
                system=system, tools=[EDIT_TOOL],
                tool_choice={"type": "tool", "name": "submit_edit_decisions"},
                messages=[{"role": "user", "content": user}],
            )
            for b in r.content:
                if getattr(b, "type", None) == "tool_use" and b.name == "submit_edit_decisions":
                    out = {}
                    for d in b.input.get("decisions", []):
                        out[int(d["index"])] = {
                            "action": str(d.get("action", "KEEP")).upper(),
                            "trimmed_text": d.get("trimmed_text"),
                        }
                    return out
        except Exception as e:
            print(f"[tighten] SDK/tool path failed ({e}); trying claude CLI", file=sys.stderr)
    proc = subprocess.run(
        ["claude", "-p", f"{system}\n\n---INPUT---\n{user}"],
        capture_output=True, text=True, timeout=900,
    )
    if proc.returncode != 0:
        sys.exit(f"[tighten] claude CLI failed: {proc.stderr[:300]}")
    return parse_decision_lines(proc.stdout)


def parse_decision_lines(text: str) -> dict[int, dict]:
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*\[(\d+)\]\s*(KEEP|REMOVE|TRIM)\s*:?\s*(.*)$", line.strip(), re.I)
        if not m:
            continue
        idx, act, rest = int(m.group(1)), m.group(2).upper(), m.group(3).strip()
        out[idx] = {"action": act, "trimmed_text": rest if act == "TRIM" else None}
    return out


# ---- transcript loading: utterances with words --------------------------------------------------

def load_utterances(p: Path) -> list[dict]:
    d = json.loads(p.read_text())
    segs = d.get("segments") or d.get("utterances") or d.get("transcript") or []
    utts = []
    for s in segs:
        if not isinstance(s, dict):
            continue
        utts.append({
            "start": float(s.get("start", 0) or 0),
            "end": float(s.get("end", 0) or 0),
            "text": str(s.get("text", "")).strip(),
            "speaker": s.get("speaker"),
            "words": s.get("words") or [],
        })
    return utts


def trim_to_cuts(utt: dict, trimmed_text: str) -> list[dict]:
    """Find the contiguous run of original words matching trimmed_text; return drop intervals
    for the leading + trailing words outside that run. Falls back to dropping nothing if the words
    list is missing (can't safely locate a sub-range without timing)."""
    words = utt.get("words") or []
    if not words:
        return []  # no word timing: leave whole utterance (KEEP-equivalent) rather than guess
    orig = [norm(w.get("word", "")) for w in words]
    want = [norm(t) for t in trimmed_text.split() if norm(t)]
    if not want:
        return [{"start": utt["start"], "end": utt["end"], "reason": "trim->empty"}]
    # locate `want` as a contiguous sublist of `orig` (best-effort: first match).
    start_i = None
    for i in range(0, len(orig) - len(want) + 1):
        if orig[i:i + len(want)] == want:
            start_i = i
            break
    if start_i is None:
        # words were tightened internally (stutters dropped) — apmontserratte by anchoring first & last.
        try:
            start_i = orig.index(want[0])
            end_i = len(orig) - 1 - orig[::-1].index(want[-1])
        except ValueError:
            return []  # can't anchor; keep whole utterance
    else:
        end_i = start_i + len(want) - 1
    cuts = []
    if start_i > 0:
        cuts.append({"start": utt["start"],
                     "end": float(words[start_i]["start"]), "reason": "trim-lead"})
    if end_i < len(words) - 1:
        cuts.append({"start": float(words[end_i]["end"]),
                     "end": utt["end"], "reason": "trim-tail"})
    return [c for c in cuts if c["end"] > c["start"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript", required=True,
                    help="ONE exchange as {segments:[{start,end,text,words:[{word,start,end}]}]}")
    ap.add_argument("--format", default="qa", choices=["qa", "hotline"])
    ap.add_argument("--shorts", action="store_true",
                    help="SHORTS mode (Devin/the reference editor recipe): 45-90s, open COLD on caller, end on Speaker's "
                         "principle, ONE arc. Uses tam_tighten_short.md. Default (mids) = content-driven, no cap.")
    ap.add_argument("--max-words", type=int, default=None,
                    help="SHORTS target kept-words nudge (default ~230 ≈ 60s @ 230wpm). Implies --shorts.")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    tp = Path(a.transcript).expanduser()
    if not tp.exists():
        sys.exit(f"[tighten] transcript not found: {tp}")
    utts = load_utterances(tp)
    if not utts:
        sys.exit("[tighten] no utterances found")

    # number the utterances for the model (speaker label if present)
    lines = []
    for i, u in enumerate(utts):
        spk = f"{u['speaker']}: " if u.get("speaker") else ""
        lines.append(f"[{i}] {spk}{u['text']}")
    if a.shorts or a.max_words:  # SHORTS mode — clean shorts prompt (no contradictory mids deletions)
        system = SHORT_PROMPT_PATH.read_text()
        tgt = a.max_words or 230
        system += (f"\n\n<length_target>Aim for ~{tgt} kept words (≈ {round(tgt/230*60)}s at 230 wpm). "
                   f"Stay inside the 45–90s band; if the read runs over, cut more from the meat.</length_target>")
    else:
        system = PROMPT_PATH.read_text()
    user = f"FORMAT: {a.format}.\n\nRAW TRANSCRIPT:\n" + "\n".join(lines) + "\n\nDECISIONS:"

    decisions = call_decisions(system, user, len(utts))

    # Build cut intervals + kept text. Missing index -> KEEP (CLIPPER_X fillMissing semantics).
    cuts, kept = [], []
    missing = []
    for i, u in enumerate(utts):
        d = decisions.get(i)
        if d is None:
            missing.append(i)
            kept.append(u["text"])
            continue
        act = d["action"]
        if act == "REMOVE":
            cuts.append({"start": u["start"], "end": u["end"], "reason": "remove"})
        elif act == "TRIM":
            tt = d.get("trimmed_text") or ""
            for c in trim_to_cuts(u, tt):
                cuts.append(c)
            kept.append(tt or u["text"])
        else:  # KEEP
            kept.append(u["text"])
    if missing:
        print(f"[tighten] {len(missing)} utterance(s) had no decision -> KEEP: {missing}",
              file=sys.stderr)

    # merge overlapping cuts
    cuts.sort(key=lambda c: c["start"])
    merged = []
    for c in cuts:
        if merged and c["start"] <= merged[-1]["end"] + 1e-3:
            merged[-1]["end"] = max(merged[-1]["end"], c["end"])
        else:
            merged.append(dict(c))

    out_path = (Path(a.out).expanduser() if a.out else tp.with_suffix(".cuts.json"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        {"cuts": merged, "kept_text": " ".join(t for t in kept if t).strip()}, indent=2))
    dropped = sum(c["end"] - c["start"] for c in merged)
    print(f"[tighten] {len(merged)} cut interval(s), ~{dropped:.1f}s dropped → {out_path}")


if __name__ == "__main__":
    main()
