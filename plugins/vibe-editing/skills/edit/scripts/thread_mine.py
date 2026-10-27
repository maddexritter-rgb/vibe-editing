#!/usr/bin/env python3
"""thread_mine.py — find cross-timeline narrative threads in long-form footage.

Unlike tam_select (which finds isolated clip-worthy moments), this reads the FULL transcript
and finds moments from DIFFERENT parts of the timeline that connect into one cohesive clip:
hook from minute 6 + story from minute 50 + payoff from minute 60.

Output: <out>.threads.json — one entry per thread, each with timestamped moments that feed
directly into script-cut as non-contiguous structure.json chunks.

Usage:
    python3 thread_mine.py --transcript pod.json --top 8 --out ~/Downloads/pod
    python3 thread_mine.py --transcript pod.json --format qa --top 5 --out ~/Downloads/pod
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

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "thread_mine.md"


def load_transcript(p: Path) -> str:
    raw = p.read_text()
    if p.suffix.lower() == ".json":
        try:
            d = json.loads(raw)
        except Exception:
            return raw
        # flat word list → sentence-chunked text with timestamps
        if isinstance(d, list) and d and isinstance(d[0], dict) and "start" in d[0]:
            return _words_to_timestamped(d)
        segs = d.get("segments") or d.get("transcript") or []
        if isinstance(segs, list) and segs and isinstance(segs[0], dict):
            # check if segments have words (Groq/WhisperX style)
            if segs[0].get("words"):
                words = []
                for seg in segs:
                    words.extend(seg.get("words", []))
                return _words_to_timestamped(words)
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


def _words_to_timestamped(words: list) -> str:
    """Convert a flat word list into timestamped sentence chunks for the LLM."""
    lines = []
    chunk_words = []
    chunk_start = None
    for w in words:
        word = (w.get("word") or w.get("text") or "").strip()
        if not word:
            continue
        if chunk_start is None:
            chunk_start = float(w.get("start", 0))
        chunk_words.append(word)
        # sentence boundary or 15-word chunk → flush
        if (word.rstrip().endswith(('.', '!', '?')) or len(chunk_words) >= 15):
            end = float(w.get("end", w.get("start", 0)))
            ts_s = f"{int(chunk_start // 60):02d}:{int(chunk_start % 60):02d}"
            ts_e = f"{int(end // 60):02d}:{int(end % 60):02d}"
            lines.append(f"[{ts_s}-{ts_e}] {' '.join(chunk_words)}")
            chunk_words = []
            chunk_start = None
    if chunk_words and chunk_start is not None:
        ts_s = f"{int(chunk_start // 60):02d}:{int(chunk_start % 60):02d}"
        lines.append(f"[{ts_s}] {' '.join(chunk_words)}")
    return "\n".join(lines)


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
                model="claude-sonnet-4-6", max_tokens=8192, temperature=0.3,
                system=system, messages=[{"role": "user", "content": user}],
            )
            return r.content[0].text
        except Exception as e:
            print(f"[thread] SDK path failed ({e}); trying claude CLI", file=sys.stderr)
    proc = subprocess.run(
        ["claude", "-p", f"{system}\n\n---INPUT---\n{user}"],
        capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        sys.exit(f"[thread] claude CLI failed: {proc.stderr[:300]}")
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


def mmss_to_sec(mmss: str) -> float:
    """Convert MM:SS or HH:MM:SS to seconds."""
    parts = mmss.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return float(mmss)


def threads_to_structures(threads: list) -> list:
    """Convert thread_mine output → script-cut compatible structure.jsons.

    Each thread becomes one clip with non-contiguous chunks (one per moment).
    The chunks can come from anywhere in the timeline — script-cut handles each independently.
    """
    structures = []
    for t in threads:
        slug = t.get("slug", f"thread_{t.get('rank', 0)}")
        segments = []
        for m in t.get("moments", []):
            start = mmss_to_sec(m["start"])
            end = mmss_to_sec(m["end"])
            segments.append({
                "in": start,
                "out": end,
                "role": m.get("role", "body"),
                "words": m.get("words", ""),
            })
        structures.append({
            "slug": slug,
            "thread_type": t.get("thread_type", "theme"),
            "thesis": t.get("thesis", ""),
            "energy_match": t.get("energy_match", "medium"),
            "segments": segments,
        })
    return structures


def main():
    ap = argparse.ArgumentParser(description="Mine cross-timeline narrative threads from long-form footage.")
    ap.add_argument("--transcript", required=True, help="Word-level or segment-level transcript JSON/TXT")
    ap.add_argument("--top", type=int, default=8, help="Max threads to return")
    ap.add_argument("--format", default="monologue", choices=["qa", "hotline", "podcast", "monologue"],
                    help="Footage type — adjusts mining heuristics")
    ap.add_argument("--out", default=None, help="Output path base (writes .threads.json + .threads.md + per-clip structure.jsons)")
    ap.add_argument("--min-gap", type=float, default=30.0,
                    help="Minimum seconds between moments for a thread to qualify (default: 30)")
    a = ap.parse_args()

    tp = Path(a.transcript).expanduser()
    if not tp.exists():
        sys.exit(f"[thread] transcript not found: {tp}")

    transcript = load_transcript(tp)
    system = PROMPT_PATH.read_text()
    user = (
        f"FORMAT: {a.format}. Return up to {a.top} threads.\n"
        f"MINIMUM GAP: moments must be at least {a.min_gap:.0f}s apart in the source.\n"
        f"Total transcript length: {len(transcript.splitlines())} lines.\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )

    print(f"[thread] mining {tp.name} for cross-timeline threads ({a.format}, top {a.top})...", flush=True)
    out = parse_json(call_claude(system, user))
    threads = out.get("threads", [])[:a.top]

    # filter: all moments must be ≥min_gap apart
    valid = []
    for t in threads:
        moments = t.get("moments", [])
        if len(moments) < 2:
            continue
        times = sorted(mmss_to_sec(m["start"]) for m in moments)
        max_gap = max(times[i+1] - times[i] for i in range(len(times)-1))
        if max_gap >= a.min_gap:
            valid.append(t)
    threads = valid

    # write outputs
    base = Path(a.out).expanduser() if a.out else tp.with_suffix("")
    base.parent.mkdir(parents=True, exist_ok=True)

    # 1. raw threads JSON
    threads_path = base.with_suffix(".threads.json")
    threads_path.write_text(json.dumps({"threads": threads}, indent=2))

    # 2. readable markdown
    md_path = base.with_suffix(".threads.md")
    lines = [f"# Thread Mine — {tp.name}", "",
             f"{len(threads)} cross-timeline threads (ranked by narrative improvement over chronological clips)", ""]
    for t in threads:
        lines.append(f"## {t.get('rank', '?')}. [{t.get('thread_type', '?').upper()}] {t.get('slug', '?')}")
        lines.append(f"**Thesis:** {t.get('thesis', '')}")
        lines.append(f"**Energy:** {t.get('energy_match', '?')} · **Est. duration:** ~{t.get('estimated_duration_s', '?')}s")
        lines.append("")
        for m in t.get("moments", []):
            lines.append(f"  - **{m.get('role', '?').upper()}** [{m.get('start')}-{m.get('end')}]: {m.get('words', '')[:120]}...")
            lines.append(f"    _{m.get('why', '')}_")
        if t.get("seam_notes"):
            lines.append(f"  - ⚠ Seam notes: {t['seam_notes']}")
        lines.append(f"  - Why thread: {t.get('why_thread', '')}")
        lines.append("")
    md_path.write_text("\n".join(lines))

    # 3. per-thread structure.json (script-cut compatible)
    structures = threads_to_structures(threads)
    struct_dir = base.parent / f"{base.stem}_thread_structures"
    struct_dir.mkdir(exist_ok=True)
    for s in structures:
        sp = struct_dir / f"{s['slug']}.json"
        # script-cut format: {"name_slug", "theme", "segments":[{"in","out"}]}
        spec = {
            "name_slug": s["slug"],
            "theme": s["thesis"],
            "thread_type": s["thread_type"],
            "segments": [{"in": seg["in"], "out": seg["out"]} for seg in s["segments"]],
        }
        sp.write_text(json.dumps(spec, indent=2))

    print(f"[thread] {len(threads)} threads → {threads_path}")
    print(f"[thread] readable  → {md_path}")
    print(f"[thread] structures → {struct_dir}/")
    for s in structures:
        moments = s["segments"]
        span = f"{moments[0]['in']:.0f}s → {moments[-1]['out']:.0f}s" if moments else "?"
        print(f"  {s['slug']:30s}  {len(moments)} moments  ({span})  [{s['thread_type']}]")

    return threads


if __name__ == "__main__":
    main()
