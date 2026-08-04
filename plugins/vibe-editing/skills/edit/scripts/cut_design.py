#!/usr/bin/env python3
"""cut_design — the SELF-CORRECTING Q&A cut designer (2026-06-17).

The fix for "the skill KNOWS the rules but applies them unevenly" (cold-start scored 58/100 while a
carefully-applied cut scores ~89). This moves the editorial gate FROM a post-hoc pass/fail INTO the
design step: it GENERATES a cut with the CLIPPER method + the gold worked examples, SCORES it with
qa_editorial_score, and if it fails, feeds the gate's exact failures back and REGENERATES — it will not
emit a cut until it PASSES (or it exhausts the iteration budget, then returns the best attempt + why).

It reads the live skill files at runtime (clipper_ai_prompt.md, qa_worked_examples.md, QA_MASTER_SOP.md)
so it always cuts to the current standard — improve those files and this improves with them.

Usage:
  cut_design.py --raw <raw_interaction.txt> [--precontext "earlier line(s)"] [--max-iters 3] [--model claude-opus-4-8]
  cut_design.py --raw - < raw.txt
Prints the winning cut transcript + its gate verdict + how many iterations it took. Exit 0 if it PASSED.
"""
from __future__ import annotations
# ── vibe-editing portable path bootstrap (auto-inserted) ──
import os as _os, sys as _sys
import pathlib as _pl
def _acq_root():
    r = _os.environ.get("VIBE_PIPELINE_ROOT") or _os.environ.get("CLAUDE_PLUGIN_ROOT")
    if r and _os.path.isdir(_os.path.join(r, ".claude-plugin")):
        return r
    d = _os.path.dirname(_os.path.abspath(__file__))
    while d != _os.path.dirname(d):
        if _os.path.isdir(_os.path.join(d, ".claude-plugin")):
            return d
        d = _os.path.dirname(d)
    return _os.path.dirname(_os.path.abspath(__file__))
VIBE_ROOT = _acq_root()
VIBE_SHARED = _os.path.join(VIBE_ROOT, "lib", "_shared")
VIBE_SKILLS = _os.path.join(VIBE_ROOT, "skills")
VIBE_VAULT  = _os.path.join(VIBE_ROOT, "vault")
VIBE_ASSETS = _os.environ.get("VIBE_ASSETS") or _os.path.join(VIBE_ROOT, "assets")
def _acq(p):
    parts = [x for x in str(p).strip("/").split("/") if x]
    if parts and parts[0] == "_shared":
        return _pl.Path(_os.path.join(VIBE_ROOT, "lib", *parts))
    return _pl.Path(_os.path.join(VIBE_SKILLS, *parts))
def _acqv(p):
    return _pl.Path(_os.path.join(VIBE_VAULT, *[x for x in str(p).strip("/").split("/") if x]))
if VIBE_SHARED not in _sys.path:
    _sys.path.insert(0, VIBE_SHARED)
# ── end bootstrap ──
import argparse, json, os, re, subprocess, sys
from pathlib import Path

REF = Path(_acq("edit/references"))
DEFAULT_MODEL = "claude-opus-4-8"

def _read(name, head=None):
    p = REF / name
    if not p.exists(): return ""
    t = p.read_text()
    return t[:head] if head else t

def generate(raw, precontext, feedback, model):
    """One generation pass. Returns {final_transcript, hook_line, payoff_last_line, est_seconds}."""
    method = _read("clipper_ai_prompt.md")
    examples = _read("qa_worked_examples.md")
    fb = ""
    if feedback:
        fb = ("\n\nYOUR PREVIOUS CUT FAILED THE EDITORIAL GATE on these points — FIX THEM and regenerate:\n"
              + "\n".join(f"  - {x}" for x in feedback) + "\n")
    pre = f"\nPRECONTEXT (earlier in the raw, available to reach back to for the hook): {precontext}\n" if precontext else ""
    prompt = f"""{method}

=== GOLD WORKED EXAMPLES — pattern-match your cut to these ===
{examples}

=== YOUR TASK ===
Cut the RAW Q&A interaction below into ONE finished clip following the method + the worked examples.
Keep ONLY words actually spoken in the raw (you may trim/drop, never invent or reorder across speakers).
Contrast hook (what they do → revenue) · ONE problem · keep Speaker's full reasoning ladder · cut every
diagnostic detour once the answer lands · END HARD on the portable principle / quantified result /
commitment (never a tactic, wind-down, open question, or restatement). Q&A diagnostic clips run ~90–150s
(a tight one can be ~70s); never a sub-40-word skeleton.{pre}{fb}
RAW:
\"\"\"
{raw}
\"\"\"

Return VALID JSON ONLY:
{{"final_transcript": "the kept spoken words in order, as one string",
  "hook_line": "the exact opening line", "payoff_last_line": "the exact final line",
  "est_seconds": <number>}}"""
    try:
        r = subprocess.run(["claude", "-p", "--model", model, prompt],
                           capture_output=True, text=True, timeout=300)
        m = re.search(r"\{[\s\S]*\}", r.stdout)
        if not m: return {"error": f"no JSON: {r.stdout[:200]} {r.stderr[:160]}"}
        return json.loads(m.group(0))
    except Exception as e:
        return {"error": str(e)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True, help="raw interaction transcript file (or - for stdin)")
    ap.add_argument("--precontext", default="")
    ap.add_argument("--max-iters", type=int, default=3)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out", help="write the winning cut JSON here")
    a = ap.parse_args()

    raw = sys.stdin.read() if a.raw == "-" else Path(a.raw).read_text()

    # import the gate's scorer (same dir)
    sys.path.insert(0, str(Path(__file__).parent))
    from qa_editorial_score import score_transcript

    feedback, best = [], None
    for i in range(1, a.max_iters + 1):
        print(f"\n── iteration {i}/{a.max_iters} ── generating…", file=sys.stderr)
        cut = generate(raw, a.precontext, feedback, a.model)
        if "error" in cut:
            print(f"  generation error: {cut['error']}", file=sys.stderr); continue
        tx = cut.get("final_transcript", "")
        verdict = score_transcript(f"cut-iter{i}", tx)
        v = verdict.get("verdict", "?")
        cut["_verdict"] = verdict
        print(f"  iter {i}: {v}  ({verdict.get('word_count')}w ~{cut.get('est_seconds')}s)  "
              f"hook={verdict.get('hook_class','?')} payoff={verdict.get('payoff_class','?')}", file=sys.stderr)
        for f in verdict.get("failures", []): print(f"      ✗ {f}", file=sys.stderr)
        if best is None or v == "PASS":
            best = cut
        if v == "PASS":
            print(f"  ✅ PASSED on iteration {i}", file=sys.stderr); break
        feedback = verdict.get("failures", []) or [verdict.get("fix", "tighten hook + payoff")]
    else:
        print(f"\n  ⚠️ no PASS in {a.max_iters} iters — returning best attempt (still gate-FAILS; review).", file=sys.stderr)

    out = {"final_transcript": best.get("final_transcript",""), "hook_line": best.get("hook_line",""),
           "payoff_last_line": best.get("payoff_last_line",""), "est_seconds": best.get("est_seconds"),
           "verdict": best.get("_verdict",{}).get("verdict","?")}
    print("\n" + "="*80 + "\nFINAL CUT  [gate: " + out["verdict"] + "]\n" + "="*80)
    print(out["final_transcript"])
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1)); print(f"\n(written to {a.out})", file=sys.stderr)
    sys.exit(0 if out["verdict"] == "PASS" else 1)

if __name__ == "__main__":
    main()
