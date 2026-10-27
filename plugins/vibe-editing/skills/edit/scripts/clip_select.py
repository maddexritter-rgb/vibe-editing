#!/usr/bin/env python3
"""clip_select — pick clip-worthy moments out of a long-form MONOLOGUE / podcast / talk.

The CLIPS-lane selector (bucket 1). NOT for Q&A / hotline — that's tam_select.py, untouched.

THREE stages:
  1. PROPOSE (LLM, prompts/clip_select.md): candidate windows + open/exit/structure tags + the
     exact open/exit lines, grounded in the 602-pair lift study.
  2. LIFT SCORE (deterministic, config/clip_lift.json): a coarse empirical prior on the tags
     (0.35*open + 0.40*exit + 0.25*structure). Good recall, coarse ranking (many candidates tie).
  3. JUDGE (LLM, prompts/clip_judge.md): a content-reading clip-worthiness re-rank of the top-K
     lift candidates — the tie-breaker that reads the ACTUAL open/exit language (hook punch,
     cold-viewer self-containment, payoff landing). Phase-5 validation showed stage 2 alone
     ranks the human's moment well into the list but rarely #1; the judge is what fixes that.

Final order = judged candidates by quality desc (lift tiebreak), then any unjudged by lift.
verdict (judged): MINE >= 70 · MAYBE >= 50 · PASS < 50.  Disable the judge with --no-judge.

Input  : transcript JSON ({"segments":[{start,end,text}]}) or plain .txt
Output : <out>.clips.json (ranked, scored) + <out>.clips.md (readable)
Auth   : ANTHROPIC_API_KEY (env / ~/.zshrc) else the `claude -p` CLI.
Usage  : python3 clip_select.py --transcript pod.json --top 12 --out 10_WORK/pod
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

HERE = Path(__file__).resolve().parent
PROMPT_PATH = HERE.parent / "prompts" / "clip_select.md"
JUDGE_PROMPT_PATH = HERE.parent / "prompts" / "clip_judge.md"
RULES_PATH = HERE.parent / "config" / "clip_lift.json"
W_OPEN, W_EXIT, W_STRUCT = 0.35, 0.40, 0.25
JUDGE_TOP_K = 8


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
                for x in segs)
        if isinstance(d, dict) and d.get("text"):
            return d["text"]
    return raw


def get_key():
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


def call_claude(system: str, user: str) -> str:
    key = get_key()
    if key:
        try:
            import anthropic
            c = anthropic.Anthropic(api_key=key)
            r = c.messages.create(model="claude-sonnet-4-6", max_tokens=8192, temperature=0,
                                  system=system, messages=[{"role": "user", "content": user}])
            return r.content[0].text
        except Exception as e:
            print(f"[clip_select] SDK failed ({e}); trying claude CLI", file=sys.stderr)
    import shutil as _shutil
    if not _shutil.which("claude"):
        sys.exit("[clip_select] No ANTHROPIC_API_KEY and no `claude` CLI on PATH — this miner needs "
                 "an LLM. Either set ANTHROPIC_API_KEY, or skip it and use the orchestrator's key-free "
                 "TEXT-FIRST selection (edit/SKILL.md Step 3c). Not hanging.")
    proc = subprocess.run(["claude", "-p", f"{system}\n\n---INPUT---\n{user}"],
                          capture_output=True, text=True, timeout=240)
    if proc.returncode != 0:
        sys.exit(f"[clip_select] claude CLI failed: {proc.stderr[:300]}")
    return proc.stdout


def parse_json(text: str) -> dict:
    t = re.sub(r"^```(?:json)?\s*", "", text.strip()).replace("```", "").strip()
    i = t.find("{")
    if i > 0:
        t = t[i:]
    j = t.rfind("}")
    if j != -1:
        t = t[:j + 1]
    return json.loads(t)


def lift_of(table: dict, tag) -> float:
    if not tag:
        return 1.0
    e = table.get(str(tag).strip().lower())
    return float(e["lift"]) if e and "lift" in e else 1.0


def _nk(s):
    return re.sub(r"[^a-z0-9 ]", "", " ".join((s or "").lower().split()))


def judge_rerank(transcript: str, cands: list, top_k: int = JUDGE_TOP_K) -> list:
    """Stage 3: content-reading clip-worthiness re-rank of the top_k lift candidates."""
    pool, rest = cands[:top_k], cands[top_k:]
    if not pool:
        return cands
    items = [{"open_line": c.get("open_line", ""), "exit_line": c.get("exit_line", "")} for c in pool]
    user = ("CANDIDATES (judge clip-worthiness, rank best first):\n" + json.dumps(items, indent=2)
            + f"\n\nSOURCE TRANSCRIPT:\n{transcript}")
    try:
        ranked = parse_json(call_claude(JUDGE_PROMPT_PATH.read_text(), user)).get("ranked", [])
    except Exception as e:
        print(f"[clip_select] judge failed ({e}); keeping lift order", file=sys.stderr)
        return cands
    for c in pool:
        ck = _nk(c.get("open_line", ""))[:50]
        for r in ranked:
            rk = _nk(r.get("open_line", ""))
            if ck and (ck == rk[:50] or ck in rk or rk[:40] in _nk(c.get("open_line", ""))):
                c["quality"] = r.get("quality")
                c["judge"] = {k: r.get(k) for k in ("hook", "cold", "payoff", "vehicle", "tight", "verdict")}
                break
    judged = [c for c in pool if c.get("quality") is not None]
    unjudged = [c for c in pool if c.get("quality") is None] + rest
    judged.sort(key=lambda c: (-(c.get("quality") or 0), -c.get("lift_score", 0)))
    for c in judged:
        q = c["quality"]
        c["verdict"] = "MINE" if q >= 70 else ("MAYBE" if q >= 50 else "PASS")
    return judged + unjudged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--out", default=None)
    ap.add_argument("--rules", default=str(RULES_PATH))
    ap.add_argument("--no-judge", dest="judge", action="store_false", help="skip the stage-3 quality judge")
    a = ap.parse_args()

    tp = Path(a.transcript).expanduser()
    if not tp.exists():
        sys.exit(f"[clip_select] transcript not found: {tp}")
    rules = json.loads(Path(a.rules).read_text())
    open_t, exit_t, struct_t = rules["open_type_lift"], rules["exit_type_lift"], rules["structure_lift"]

    transcript = load_transcript(tp)
    out = parse_json(call_claude(PROMPT_PATH.read_text(), f"Return up to {a.top} candidates.\n\nTRANSCRIPT:\n{transcript}"))
    cands = out.get("candidates", [])

    # stage 2: lift score + coarse sort
    for c in cands:
        ol, xl, sl = lift_of(open_t, c.get("open_type")), lift_of(exit_t, c.get("exit_type")), lift_of(struct_t, c.get("structure"))
        c["lift_breakdown"] = {"open": ol, "exit": xl, "structure": sl}
        c["lift_score"] = round(W_OPEN * ol + W_EXIT * xl + W_STRUCT * sl, 3)
        c["verdict"] = "MINE" if c["lift_score"] >= 1.25 else ("MAYBE" if c["lift_score"] >= 1.0 else "PASS")
    cands.sort(key=lambda c: -c.get("lift_score", 0))

    # stage 3: quality judge re-rank (the tie-breaker)
    if a.judge:
        cands = judge_rerank(transcript, cands)
    for i, c in enumerate(cands, 1):
        c["rank"] = i
    cands = cands[: a.top]

    base = Path(a.out).expanduser() if a.out else tp.with_suffix("")
    js, md = base.with_suffix(".clips.json"), base.with_suffix(".clips.md")
    js.write_text(json.dumps({"candidates": cands, "scoring": {
        "weights": {"open": W_OPEN, "exit": W_EXIT, "structure": W_STRUCT},
        "judge": a.judge, "rules_subset": rules.get("subset"), "n_pairs": rules.get("n_pairs")}}, indent=2))

    lines = [f"# CLIP selection — {tp.name}", "",
             f"{len(cands)} candidates · lift prior + {'quality judge' if a.judge else 'lift only'} ({rules.get('n_pairs')} pairs) · best first", ""]
    for c in cands:
        q = f" · quality {c['quality']}" if c.get("quality") is not None else ""
        lines += [
            f"## [{c['verdict']}] {c['rank']}. {c.get('title_idea','')}  "
            f"({c.get('start')}–{c.get('end')}) · lift {c.get('lift_score')}{q}",
            f"- open ({c.get('open_type')}): \"{c.get('open_line','')}\"",
            f"- exit ({c.get('exit_type')}): \"{c.get('exit_line','')}\"",
            f"- structure ({c.get('structure')}) · reach_back={c.get('reach_back')}",
            f"- keep: {c.get('keep_vehicle','')}",
            f"- cut: {', '.join(c.get('cut_list', []) or [])}"]
        if c.get("judge"):
            lines.append(f"- judge: {c['judge'].get('verdict','')}")
        lines += [f"- why: {c.get('why','')}", ""]
    md.write_text("\n".join(lines))

    mine = sum(1 for c in cands if c["verdict"] == "MINE")
    print(f"[clip_select] {len(cands)} candidates ({mine} MINE){' +judge' if a.judge else ''} → {js}  +  {md}")


if __name__ == "__main__":
    main()
