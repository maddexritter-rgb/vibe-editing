#!/usr/bin/env python3
"""qa_gauntlet.py — THE pre-delivery gate battery. Runs every deterministic gate in one shot and BLOCKS
delivery (exit 1) until they pass. This moves QA from AFTER delivery (a human catches it = a round-trip) to
BEFORE delivery (a tool catches it in-loop). The making session MUST run this as the last step before
collecting a Q&A clip; do not deliver a clip the gauntlet failed.

Runs (each SKIPPED if its inputs are absent, never silently passed):
  1. qa_editorial_score.py  — hook/payoff/portability/one-arc/substance        (needs a transcript)
  2. qa_prebuild_audit.py   — G1-G8 incl. ENDING-COMPLETE + PACING             (needs --project with edl.json + words)
  3. framing_gate.py        — single-cam face-too-small / too-wide             (needs --clip)
  4. reqc.py                — res/loudness/ending/jump-cut/harsh-seam/mechanics (needs --clip; GROQ_API_KEY)
  5. revision_verify.py     — note-application + no regression (REVISIONS only) (needs --prior + --spec)
Convention: gate exit 0 = PASS · 1 = FAIL (blocks) · 2 = setup/env skip (e.g. reqc with no GROQ key).

After this passes, the orchestrator STILL runs the LLM auditors (audit-captions / audit-visual / audit-audio) —
those aren't scripts. The gauntlet prints that checklist.

Usage:
  qa_gauntlet.py --clip DELIVERED.mp4 [--project DIR] [--transcript w_norm.json]
                 [--single-cam-span "S E"]            # restrict framing_gate to the single-cam answer
                 [--prior PRIOR_w_norm --spec spec.json]   # revision mode
Exit 0 = all run gates passed (clear to deliver after the agent-audits) · 1 = a gate FAILED.
"""
import argparse, subprocess, sys, json, glob, os, tempfile
from pathlib import Path

SK = Path.home() / ".claude/skills"
EDIT = SK / "edit/scripts"
SHARED = SK / "_shared"

def run(name, cmd):
    """Return (name, status, tail). status in PASS/FAIL/SKIP."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except Exception as e:
        return (name, "FAIL", f"could not run: {e}")
    tail = (r.stdout + r.stderr).strip().splitlines()
    tail = "\n      ".join(tail[-4:]) if tail else ""
    if r.returncode == 0: return (name, "PASS", tail)
    if r.returncode == 2: return (name, "SKIP", tail + "  (setup/env — gate did not run)")
    return (name, "FAIL", tail)

def transcript_text(w_norm_path):
    d = json.loads(Path(w_norm_path).read_text())
    w = d.get("words", d) if isinstance(d, dict) else d
    return " ".join((x["word"] if isinstance(x, dict) else x) for x in w)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", required=True)
    ap.add_argument("--project")
    ap.add_argument("--transcript")
    ap.add_argument("--single-cam-span")
    ap.add_argument("--cc", help="burned caption .ass (enables caption_sync_gate: do captions match the delivered audio?)")
    ap.add_argument("--prior")
    ap.add_argument("--spec")
    a = ap.parse_args()
    results = []

    # resolve transcript (explicit > newest w_norm under --project)
    tr = a.transcript
    if not tr and a.project:
        cands = sorted(glob.glob(f"{a.project}/**/w_norm.json", recursive=True), key=os.path.getmtime)
        tr = cands[-1] if cands else None

    # 1. EDITORIAL
    if tr and Path(tr).exists():
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
            tf.write(transcript_text(tr)); txt = tf.name
        results.append(run("editorial", [sys.executable, str(EDIT / "qa_editorial_score.py"), "--text", open(txt).read()]))
        os.unlink(txt)
    else:
        results.append(("editorial", "SKIP", "no transcript (pass --transcript or --project)"))

    # 2. PREBUILD (G1-G8) — needs edl.json
    edl = Path(a.project) / "edl.json" if a.project else None
    if edl and edl.exists():
        results.append(run("prebuild(G1-G8)", [sys.executable, str(EDIT / "qa_prebuild_audit.py"), a.project]))
    else:
        results.append(("prebuild(G1-G8)", "SKIP", f"no edl.json at {edl}"))

    # 3. FRAMING
    fcmd = [sys.executable, str(EDIT / "framing_gate.py"), "--clip", a.clip]
    if a.single_cam_span:
        s, e = a.single_cam_span.split()
        fcmd += ["--start", s, "--end", e]
    results.append(run("framing", fcmd))

    # 3b. DISFLUENCY (ACOUSTIC um/ah + long pause) — catches what the transcript hides (ASR drops ums + swallows pauses)
    dcmd = [sys.executable, str(EDIT / "disfluency_gate.py"), "--clip", a.clip]
    if tr and Path(tr).exists(): dcmd += ["--words", tr]
    results.append(run("disfluency", dcmd))

    # 3c. CAPTION-SYNC — do the burned captions match the delivered audio? (catches the 'different'/'right' desync)
    if a.cc and Path(a.cc).exists():
        results.append(run("caption-sync", [sys.executable, str(EDIT / "caption_sync_gate.py"), "--cc", a.cc, "--clip", a.clip, "--max-mismatch", "2"]))
    else:
        results.append(("caption-sync", "SKIP", "no --cc (burned caption .ass) given"))

    # 4. REQC
    rcmd = [sys.executable, str(EDIT / "reqc.py"), a.clip]
    if a.project: rcmd += ["--project", a.project]
    results.append(run("reqc", rcmd))

    # 5. REVISION_VERIFY (revision mode)
    if a.prior and a.spec:
        results.append(run("revision_verify", [sys.executable, str(SHARED / "revision_verify.py"),
                                                "--prior", a.prior, "--new", tr or a.clip, "--spec", a.spec]))
    else:
        results.append(("revision_verify", "SKIP", "not a revision (pass --prior + --spec)"))

    print("\n========== QA GAUNTLET ==========")
    failed = []
    for name, status, tail in results:
        icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "·"}[status]
        print(f"  {icon} {status:4s} {name}")
        if status in ("FAIL", "SKIP") and tail:
            print(f"      {tail}")
        if status == "FAIL": failed.append(name)
    print("\n  Still required (LLM auditors — not scripts): audit-captions · audit-visual · audit-audio")
    if failed:
        print(f"\n🛑 BLOCK — {len(failed)} gate(s) FAILED: {', '.join(failed)}. Fix + re-run the gauntlet. Do NOT deliver.")
        return 1
    print("\n✅ All run gates PASS. Run the 3 audit agents, then deliver.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
