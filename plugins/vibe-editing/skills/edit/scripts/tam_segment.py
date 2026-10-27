#!/usr/bin/env python3
"""tam_segment — split a long-form Q&A / Hotline transcript into discrete EXCHANGES.

Ported from Julian's SEGMENTER-X (src/prompts/segment-system.ts + src/app/api/segment/route.ts).
This is the SELECT-step's upstream: it turns a 60-90 min session into clean per-guest exchanges and
quarantines the filler (host pump-up, "can you hear me", calling the next guest, ad reads) into their
own droppable segments, so only real content gets fed to tam_select.

What was ported from SEGMENTER-X:
  - the segment-system prompt (here as prompts/tam_segment.md, adapted for Speaker Q&A/Hotline + filler)
  - the transcript formatting: each line rendered as
        [LINE N] [startS-endS] text
        [WORDS N] word@ts word@ts ...
  - the timestamp-clamp guard: Claude's startSec/endSec are only trusted when they fall inside the
    claimed line range [lineStart, lineEnd]; otherwise we fall back to the line boundary.
SEGMENTER-X's known limitation: a 90-min transcript blows the single-pass token ceiling (its route
just errors and tells you to split the video). We FIX that here by windowing the transcript into
~25-30 min chunks, segmenting each, then merging with offset-corrected line indices + timestamps.

Claude-call pattern (SDK with `claude -p` CLI fallback, key from env or ~/.zshrc) is reused verbatim
from tam_select.py so this needs no new dependency beyond Anthropic.

Input  : a long-form transcript — our JSON ({"segments":[{"start","end","text"[,"words"]}]}) or .txt
Output : <out>.segments.json = {"exchanges":[{start,end,title,summary,is_filler,speaker_lead,...}]}
         + <out>.segments.md (readable table)

Usage  : python3 tam_segment.py --transcript session.json --window-min 28 --out ~/Downloads/sess
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

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "tam_segment.md"

# ----------------------------------------------------------------------------------------------
# Claude call — copied from tam_select.py (SDK first, claude CLI fallback, key from env/~/.zshrc).
# ----------------------------------------------------------------------------------------------

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


def call_claude(system: str, user: str, max_tokens: int = 16000) -> str:
    key = get_key()
    if key:
        try:
            import anthropic
            c = anthropic.Anthropic(api_key=key)
            r = c.messages.create(
                model="claude-sonnet-4-6", max_tokens=max_tokens, temperature=0,
                system=system, messages=[{"role": "user", "content": user}],
            )
            return r.content[0].text
        except Exception as e:
            print(f"[seg] SDK path failed ({e}); trying claude CLI", file=sys.stderr)
    proc = subprocess.run(
        ["claude", "-p", f"{system}\n\n---INPUT---\n{user}"],
        capture_output=True, text=True, timeout=900,
    )
    if proc.returncode != 0:
        sys.exit(f"[seg] claude CLI failed: {proc.stderr[:300]}")
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


# ----------------------------------------------------------------------------------------------
# Transcript loading -> list of line dicts {start,end,text,words?}.
# `words` (if present) is [{word,start,end}] — used for word-level precision like SEGMENTER-X.
# ----------------------------------------------------------------------------------------------

def load_lines(p: Path) -> list[dict]:
    raw = p.read_text()
    if p.suffix.lower() == ".json":
        d = json.loads(raw)
        segs = d.get("segments") or d.get("transcript") or []
        if isinstance(segs, list) and segs and isinstance(segs[0], dict):
            out = []
            for x in segs:
                out.append({
                    "start": float(x.get("start", 0) or 0),
                    "end": float(x.get("end", 0) or 0),
                    "text": str(x.get("text", "")).strip(),
                    "words": x.get("words") or [],
                })
            return out
        # Whole-transcript-as-text fallback: one synthetic line.
        if isinstance(d, dict) and d.get("text"):
            return [{"start": 0.0, "end": 0.0, "text": d["text"], "words": []}]
    # Plain .txt: split into pseudo-lines on blank lines / newlines so the model still gets [LINE N].
    chunks = [c.strip() for c in re.split(r"\n\s*\n|\r?\n", raw) if c.strip()]
    return [{"start": 0.0, "end": 0.0, "text": c, "words": []} for c in chunks]


def render_window(lines: list[dict]) -> str:
    """Format a window of lines exactly like SEGMENTER-X's /api/segment route."""
    out = []
    for i, t in enumerate(lines):
        header = f"[LINE {i}] [{t['start']:.2f}s-{t['end']:.2f}s] {t['text']}"
        words = t.get("words") or []
        if words:
            wl = " ".join(
                f"{w.get('word','')}@{float(w.get('start',0) or 0):.2f}" for w in words
            )
            out.append(f"{header}\n[WORDS {i}] {wl}")
        else:
            out.append(header)
    return "\n".join(out)


# ----------------------------------------------------------------------------------------------
# Window the transcript so we never blow the token ceiling (SEGMENTER-X's known limitation).
# Windows break on a LINE boundary near the target wall-clock duration, so no exchange is split
# *inside* a line. (An exchange that straddles a window edge will appear as two adjacent segments;
# the merge step below stitches a content segment that runs to a window's last line with the next
# window's opening content segment when they are contiguous and same-lead.)
# ----------------------------------------------------------------------------------------------

def make_windows(lines: list[dict], window_sec: float) -> list[tuple[int, list[dict]]]:
    if not lines:
        return []
    # If we have no real timestamps, fall back to ~line-count windows (~900 lines/window).
    has_time = any(l["end"] > 0 for l in lines)
    windows: list[tuple[int, list[dict]]] = []
    start_idx = 0
    if has_time:
        win_start_t = lines[0]["start"]
        for i, l in enumerate(lines):
            if l["end"] - win_start_t >= window_sec and i > start_idx:
                windows.append((start_idx, lines[start_idx:i]))
                start_idx = i
                win_start_t = l["start"]
        windows.append((start_idx, lines[start_idx:]))
    else:
        step = 900
        for s in range(0, len(lines), step):
            windows.append((s, lines[s:s + step]))
    return windows


def segment_window(system: str, lines: list[dict], fmt: str) -> list[dict]:
    """Call Claude on ONE window; clamp timestamps to line ranges (SEGMENTER-X enrich logic)."""
    body = render_window(lines)
    user = (
        f"FORMAT: {fmt}. Segment this long-form session into exchanges and carve filler.\n\n"
        f"Here is the timestamped transcript:\n\n{body}"
    )
    raw = call_claude(system, user)
    try:
        parsed = parse_json(raw)
    except Exception as e:
        sys.exit(f"[seg] could not parse model JSON for a window ({e}); preview: {raw[:300]}")
    segs = parsed.get("segments") if isinstance(parsed, dict) else parsed
    if not isinstance(segs, list) or not segs:
        sys.exit(f"[seg] model returned no segments for a window; preview: {raw[:300]}")

    n = len(lines)
    enriched = []
    for i, seg in enumerate(segs):
        start_idx = int(seg.get("startLine", 0) or 0)
        start_idx = max(0, min(start_idx, n - 1))
        if i + 1 < len(segs):
            nxt = int(segs[i + 1].get("startLine", start_idx + 1) or (start_idx + 1))
            end_idx = max(start_idx, min(nxt, n) - 1)
        else:
            end_idx = n - 1
        line_start = lines[start_idx]["start"]
        line_end = lines[end_idx]["end"]
        # SEGMENTER-X clamp: trust word-level startSec/endSec only inside [line_start, line_end].
        ss = seg.get("startSec")
        start_sec = float(ss) if isinstance(ss, (int, float)) and line_start <= ss <= line_end else line_start
        es = seg.get("endSec")
        end_sec = (
            float(es)
            if isinstance(es, (int, float)) and line_start <= es <= line_end and es > start_sec
            else line_end
        )
        enriched.append({
            "title": seg.get("title") or f"Segment {i+1}",
            "summary": seg.get("summary", ""),
            "is_filler": bool(seg.get("is_filler", str(seg.get("title", "")).strip().lower().startswith("filler"))),
            "speaker_lead": (seg.get("speaker_lead") or "host"),
            "start_line": start_idx,
            "end_line": end_idx,
            "start": round(start_sec, 3),
            "end": round(end_sec, 3),
        })
    return enriched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--format", default="qa", choices=["qa", "hotline"])
    ap.add_argument("--window-min", type=float, default=28.0,
                    help="window size in minutes (default 28; lower if you still hit token limits)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    tp = Path(a.transcript).expanduser()
    if not tp.exists():
        sys.exit(f"[seg] transcript not found: {tp}")
    lines = load_lines(tp)
    if not lines:
        sys.exit("[seg] transcript had no lines")
    system = PROMPT_PATH.read_text()

    windows = make_windows(lines, a.window_min * 60.0)
    print(f"[seg] {len(lines)} lines → {len(windows)} window(s) (~{a.window_min:.0f} min each)",
          file=sys.stderr)

    exchanges: list[dict] = []
    for wi, (offset, wlines) in enumerate(windows):
        print(f"[seg]   window {wi+1}/{len(windows)} (lines {offset}..{offset+len(wlines)-1})",
              file=sys.stderr)
        segs = segment_window(system, wlines, a.format)
        # Offset-correct line indices back into the GLOBAL transcript (timestamps are already global).
        for s in segs:
            s["start_line"] += offset
            s["end_line"] += offset
        exchanges.extend(segs)

    # Stitch a content exchange that runs to a window's final line with the next window's opening
    # content exchange when they are line-contiguous and same lead (a guest split by a window edge).
    merged: list[dict] = []
    for s in exchanges:
        if (
            merged
            and not s["is_filler"] and not merged[-1]["is_filler"]
            and s["start_line"] == merged[-1]["end_line"] + 1
            and s.get("speaker_lead") == merged[-1].get("speaker_lead")
            and s["start"] - merged[-1]["end"] < 2.0  # contiguous in time, not a real gap
        ):
            prev = merged[-1]
            prev["end_line"] = s["end_line"]
            prev["end"] = s["end"]
            prev["summary"] = (prev["summary"] + " " + s["summary"]).strip()
        else:
            merged.append(s)

    for i, s in enumerate(merged):
        s["id"] = i + 1

    base = Path(a.out).expanduser() if a.out else tp.with_suffix("")
    js = base.with_suffix(".segments.json")
    md = base.with_suffix(".segments.md")
    js.write_text(json.dumps({"format": a.format, "exchanges": merged}, indent=2))

    def hhmmss(s):
        s = int(s); return f"{s//3600:d}:{(s%3600)//60:02d}:{s%60:02d}"
    out = [f"# Segmentation — {tp.name}", "",
           f"{len(merged)} segments "
           f"({sum(1 for x in merged if not x['is_filler'])} content / "
           f"{sum(1 for x in merged if x['is_filler'])} filler)", ""]
    for s in merged:
        tag = "FILLER" if s["is_filler"] else s.get("speaker_lead", "host").upper()
        out += [f"## [{tag}] {s['id']}. {s['title']}  ({hhmmss(s['start'])}–{hhmmss(s['end'])})",
                f"- {s['summary']}", ""]
    md.write_text("\n".join(out))

    content = sum(1 for s in merged if not s["is_filler"])
    print(f"[seg] {len(merged)} segments ({content} content, {len(merged)-content} filler) → {js}  +  {md}")


if __name__ == "__main__":
    main()
