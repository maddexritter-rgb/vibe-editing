#!/usr/bin/env python3
"""tam_select — pick clip-worthy moments out of a long-form Q&A / Hotline transcript.

Scores candidate segments against the team's TAM selection filter (qa_tam_filter, April-2026
Shorts Review Bootcamp) and returns ranked MINE / MAYBE candidates with timestamps. Run this
BEFORE cutting — it answers "what's worth clipping", upstream of edit's cut step.

Input  : a transcript — our transcript JSON ({"segments":[{"start","end","text"}]}) or plain .txt
Output : <out>.tam.json (ranked candidates) + <out>.tam.md (readable table)

Auth   : ANTHROPIC_API_KEY (env, else sourced from ~/.zshrc). Falls back to the `claude -p` CLI.
Usage  : python3 tam_select.py --transcript pod.json --top 10 --out ~/Downloads/pod
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

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "tam_select.md"


def load_transcript(p: Path) -> str:
    raw = p.read_text()
    if p.suffix.lower() == ".json":
        try:
            d = json.loads(raw)
        except Exception:
            return raw
        segs = d.get("segments") or d.get("transcript") or []
        if isinstance(segs, list) and segs and isinstance(segs[0], dict):
            def ts(s):
                s = float(s or 0)
                return f"{int(s // 60):02d}:{int(s % 60):02d}"
            return "\n".join(
                f"[{ts(x.get('start', 0))}-{ts(x.get('end', 0))}] {str(x.get('text', '')).strip()}"
                for x in segs
            )
        if isinstance(d, dict) and d.get("text"):
            return d["text"]
    return raw


def get_key() -> str | None:
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k:
        return k
    try:
        z = (Path.home() / ".zshrc").read_text()
        m = re.search(r"sk-ant-[A-Za-z0-9_\-]+", z)
        if m:
            return m.group(0)
    except Exception:
        pass
    return None


def call_claude(system: str, user: str) -> str:
    key = get_key()
    if key:
        try:
            import anthropic
            c = anthropic.Anthropic(api_key=key)
            r = c.messages.create(
                model="claude-sonnet-4-6", max_tokens=8192, temperature=0,
                system=system, messages=[{"role": "user", "content": user}],
            )
            return r.content[0].text
        except Exception as e:
            print(f"[tam] SDK path failed ({e}); trying claude CLI", file=sys.stderr)
    proc = subprocess.run(
        ["claude", "-p", f"{system}\n\n---INPUT---\n{user}"],
        capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        sys.exit(f"[tam] claude CLI failed: {proc.stderr[:300]}")
    return proc.stdout


def parse_json(text: str) -> dict:
    t = re.sub(r"^```(?:json)?\s*", "", text.strip()).replace("```", "").strip()
    i = t.find("{")
    if i > 0:
        t = t[i:]
    j = t.rfind("}")
    if j != -1:
        t = t[: j + 1]
    return json.loads(t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--format", default="qa", choices=["qa", "hotline"])
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    tp = Path(a.transcript).expanduser()
    if not tp.exists():
        sys.exit(f"[tam] transcript not found: {tp}")
    transcript = load_transcript(tp)
    system = PROMPT_PATH.read_text()
    user = f"FORMAT: {a.format}. Return up to {a.top} candidates.\n\nTRANSCRIPT:\n{transcript}"

    out = parse_json(call_claude(system, user))
    cands = out.get("candidates", [])[: a.top]

    base = Path(a.out).expanduser() if a.out else tp.with_suffix("")
    js = base.with_suffix(".tam.json")
    md = base.with_suffix(".tam.md")
    js.write_text(json.dumps({"candidates": cands}, indent=2))

    lines = [f"# TAM clip-selection — {tp.name}", "", f"{len(cands)} candidates (MINE first)", ""]
    for c in cands:
        lines += [
            f"## [{c.get('verdict')}] {c.get('rank')}. {c.get('title')}  "
            f"({c.get('start')}–{c.get('end')}) · TAM {c.get('tam_score')}",
            f"- hook: {c.get('hook')}",
            f"- issue: {c.get('issue')}",
            f"- solution: {c.get('solution')}",
            f"- tension: {c.get('tension')}",
            f"- why: {c.get('why')}",
            "",
        ]
    md.write_text("\n".join(lines))

    mine = sum(1 for c in cands if c.get("verdict") == "MINE")
    print(f"[tam] {len(cands)} candidates ({mine} MINE) → {js}  +  {md}")


if __name__ == "__main__":
    main()
