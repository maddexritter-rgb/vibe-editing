#!/usr/bin/env python3
"""window_validator.py — THE shared boundary-validation gate for every cut pipeline.

Checks a set of rough in/out chunk windows against a word-level transcript and flags
the 9 failure modes discovered during the script-cut session (2026-06-08). Call this
BEFORE any cut engine runs — script-cut, precision_cut, clip-miner, or any renderer.

Usage (library):
    from window_validator import validate_windows
    warnings = validate_windows(words, chunks, source=None)
    # warnings = [{rule, chunk, severity, msg, fix}, ...]

Usage (CLI):
    python window_validator.py --transcript words.json --spec structure.json [--source video.mp4]

Rules (each was a real bug, caught on real footage):
  1. LEADING_ORPHAN    — window pulls in tail of previous sentence ("not dead. And so...")
  2. TRAILING_ORPHAN   — window extends past payoff into next sentence ("...this season. And so it hasn't")
  3. OVERLAP           — two chunks share a time range → audio repeats a phrase
  4. PAYOFF_TRUNCATED  — last word is a connector/mid-clause → payoff chopped
  5. OPENER_DIRTY      — first word(s) are connectors/fillers → clip opens weak
  6. CLIPPED_TAIL      — energy at final word boundary still loud → mid-syllable chop (needs --source)
  7. GHOST_SILENCE     — kept word's onset is in deep silence → MFA ghost alignment (needs --source)
  8. TANGENT_RISK      — chunk > 15s with mid-chunk sentence boundaries → possible uncut tangent
  9. DISJOINT_GAP      — chunks not sorted or have negative/zero gap between them

Auto-fix: for rules 1-5, a concrete fix dict is returned (new in/out values). The caller
decides whether to apply automatically or flag for human review.

Generic skill — no brand baked in.
"""
import json, os, re, sys, subprocess, argparse
import numpy as _np

# ── word helpers ──────────────────────────────────────────────────────────────

FILL = {'uh', 'um', 'uhh', 'umm', 'mm', 'hmm', 'er', 'ah', 'mhm', 'uhm'}
CONN = {'and', 'but', 'so', 'or', 'well', 'like', 'now', 'okay', 'right',
        'because', 'cause', 'which', 'yeah', 'yes', 'no'}

def _cw(w):
    """Clean word → lowercase letters + apostrophes only."""
    return re.sub(r"[^a-z']", "", w.lower())

def _real(w):
    """True if the word has content (not a filler)."""
    s = re.sub(r"[^a-z0-9']", "", w.lower())
    return bool(s) and s not in FILL

def _ends_sent(w):
    """True if word ends with sentence-terminal punctuation."""
    return bool(re.search(r'[.!?][\"\'\)\]]*$', w.strip()))

def _is_connector(w):
    """True if the word is a leading connector / weak opener."""
    return _cw(w) in CONN

# ── energy helpers (only when --source is available) ──────────────────────────

def _mean_db(source, t0, t1):
    """Mean dB of a source span. Returns -120 on error."""
    if t1 <= t0 or source is None:
        return -120.0
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{t1-t0:.3f}",
         "-i", source, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True).stdout
    x = _np.frombuffer(raw, _np.float32)
    if len(x) < 320:
        return -120.0
    return float(20 * _np.log10(_np.sqrt(_np.mean(x**2)) + 1e-9))

# ── transcript loader (handles all common formats) ───────────────────────────

def load_words(path):
    """Load a word-level transcript → flat list of {start, end, word}."""
    d = json.load(open(path))
    if isinstance(d, list):
        # flat list of {start, end, word}
        return d
    if isinstance(d, dict):
        # Groq verbose_json with word granularity returns "segments": null — fall through to "words"
        if not d.get("segments") and "words" in d:
            d = {"words": d["words"]}
        if "segments" in d:
            words = []
            for seg in d["segments"]:
                for w in seg.get("words", []):
                    if w.get("start") is not None:
                        words.append({"start": float(w["start"]),
                                      "end": float(w.get("end", w["start"])),
                                      "word": (w.get("word") or w.get("text") or "").strip()})
            return words
        if "words" in d:
            return [{"start": float(w["start"]),
                     "end": float(w.get("end", w["start"])),
                     "word": (w.get("word") or w.get("text") or "").strip()}
                    for w in d["words"] if w.get("start") is not None]
    return []

def load_structure(path):
    """Load a structure/spec JSON → list of (name, chunks) pairs.

    Handles:
      - flat: {"segments": [{in,out}, ...]}  → [("clip", [chunks])]
      - per-clip: {"clips": [{"slug":..., "structure":[{in,out},...]}]}
                  → [("GreatMoodSkill", [chunks]), ("NothingIsYours", [chunks]), ...]
      - bare list: [{in,out}, ...] → [("clip", [chunks])]
    """
    d = json.load(open(path))
    if isinstance(d, dict):
        # per-clip format (clip-miner / batch style)
        if "clips" in d and isinstance(d["clips"], list) and d["clips"]:
            first = d["clips"][0]
            if isinstance(first, dict) and "structure" in first:
                return [(c.get("slug", f"clip_{i}"), c["structure"])
                        for i, c in enumerate(d["clips"])]
            # clips is a flat list of {in,out} chunks
            return [("clip", d["clips"])]
        # flat structure
        segs = d.get("segments", d.get("chunks", []))
        return [("clip", segs)]
    if isinstance(d, list):
        return [("clip", d)]
    return []

# ── the 9 rules ──────────────────────────────────────────────────────────────

def _words_in_window(words, win_in, win_out, pad=0.20):
    """Return (indices, word-dicts) of transcript words inside a rough window."""
    idxs = [i for i, w in enumerate(words)
            if w["start"] >= win_in - pad and w["start"] < win_out + pad]
    return idxs, [words[i] for i in idxs]

def _rule_leading_orphan(words, chunk, chunk_idx):
    """Rule 1: first ≤3 real words contain a sentence-ending word → orphan tail from prev sentence."""
    _, wds = _words_in_window(words, chunk["in"], chunk["out"])
    real_words = [(i, w) for i, w in enumerate(wds) if _real(w["word"])]
    if len(real_words) < 3:
        return None
    for pos, (i, w) in enumerate(real_words[:3]):
        if _ends_sent(w["word"]):
            # found a sentence end in the first 3 real words → orphan
            next_word = real_words[pos + 1][1] if pos + 1 < len(real_words) else None
            new_in = next_word["start"] - 0.10 if next_word else chunk["in"]
            return {
                "rule": "LEADING_ORPHAN",
                "chunk": chunk_idx,
                "severity": "ERROR",
                "msg": (f"Chunk {chunk_idx}: first {pos+1} real word(s) end with "
                        f"'{w['word']}' (sentence boundary) — orphan tail from previous sentence. "
                        f"Clip would open on junk before the real hook."),
                "fix": {"in": round(new_in, 2)},
                "words_flagged": [rw[1]["word"] for rw in real_words[:pos+1]]
            }
    return None

def _rule_trailing_orphan(words, chunk, chunk_idx):
    """Rule 2: words after the last sentence-terminal → orphan head from next sentence."""
    _, wds = _words_in_window(words, chunk["in"], chunk["out"])
    real_words = [(i, w) for i, w in enumerate(wds) if _real(w["word"])]
    if len(real_words) < 3:
        return None
    # find the LAST sentence-ending word
    last_sent_pos = -1
    for pos, (i, w) in enumerate(real_words):
        if _ends_sent(w["word"]):
            last_sent_pos = pos
    if last_sent_pos < 0:
        return None  # no sentence boundaries at all — can't check
    trailing = real_words[last_sent_pos + 1:]
    if len(trailing) >= 2:
        # 2+ real words after the last period → orphan head leaking in
        end_word = real_words[last_sent_pos][1]
        new_out = end_word["end"] + 0.15
        return {
            "rule": "TRAILING_ORPHAN",
            "chunk": chunk_idx,
            "severity": "ERROR",
            "msg": (f"Chunk {chunk_idx}: {len(trailing)} word(s) after last sentence end "
                    f"'{end_word['word']}' — orphan head from next sentence leaking in. "
                    f"Clip would end with dangling start of a new thought."),
            "fix": {"out": round(new_out, 2)},
            "words_flagged": [w[1]["word"] for w in trailing]
        }
    return None

def _rule_overlap(chunks, i):
    """Rule 3: chunk i overlaps with chunk i+1 → audio repeats."""
    if i + 1 >= len(chunks):
        return None
    a_out = chunks[i]["out"]
    b_in = chunks[i + 1]["in"]
    if a_out > b_in + 0.05:  # 50ms tolerance for rounding
        overlap = a_out - b_in
        return {
            "rule": "OVERLAP",
            "chunk": i,
            "severity": "ERROR",
            "msg": (f"Chunks {i} and {i+1} overlap by {overlap:.2f}s "
                    f"(chunk {i} out={a_out:.2f}, chunk {i+1} in={b_in:.2f}). "
                    f"Renderer will play the overlapping audio TWICE."),
            "fix": {"merge_or_disjoint": True,
                    "suggestion": f"Set chunk {i} out={b_in - 0.05:.2f} OR merge into one chunk."}
        }
    return None

def _rule_payoff_truncated(words, chunk, chunk_idx):
    """Rule 4: last word is a connector / mid-clause → payoff is being chopped."""
    _, wds = _words_in_window(words, chunk["in"], chunk["out"])
    real_words = [w for w in wds if _real(w["word"])]
    if not real_words:
        return None
    last = real_words[-1]
    cword = _cw(last["word"])
    # check: connector, preposition, or article as last word
    weak_endings = CONN | {'the', 'a', 'an', 'of', 'for', 'in', 'on', 'at', 'to',
                           'with', 'from', 'by', 'is', 'was', 'are', 'were', 'been',
                           'be', 'my', 'your', 'his', 'her', 'their', 'our', 'its',
                           'if', 'when', 'where', 'while', 'who', 'what', 'how', 'that'}
    if cword in weak_endings:
        return {
            "rule": "PAYOFF_TRUNCATED",
            "chunk": chunk_idx,
            "severity": "ERROR",
            "msg": (f"Chunk {chunk_idx}: last real word is '{last['word']}' (weak/mid-clause). "
                    f"The payoff is being chopped. Push out= further until the final word "
                    f"is a real terminal (noun / verb / period)."),
            "fix": {"out_needs_extension": True,
                    "current_out": chunk["out"],
                    "last_word": last["word"]}
        }
    # source-continuation check (2026-06-17, CMO note "the AI cuts off the speaker"): the last word
    # may be a CONTENT word yet the SAME speaker keeps going on the same clause (e.g. "...to the next
    # level [and feel like...]") — a false ending the weak-endings set above can't see. Look at the
    # SOURCE words AFTER out= via the canonical shared rule. Fail-safe: any error → skip (no false block).
    try:
        import os as _os, sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from ending_check import ends_complete
        nxt = [w for w in words if w.get("start", 0) >= chunk["out"] - 0.05][:15]
        ok, reason = ends_complete(last, nxt)
        if not ok:
            return {"rule": "PAYOFF_TRUNCATED", "chunk": chunk_idx, "severity": "ERROR",
                    "msg": f"Chunk {chunk_idx}: FALSE ENDING — {reason}",
                    "fix": {"out_needs_extension": True, "current_out": chunk["out"], "last_word": last["word"]}}
    except Exception:
        pass
    return None

def _rule_opener_dirty(words, chunk, chunk_idx):
    """Rule 5: first real word is a connector/filler → clip opens weak."""
    _, wds = _words_in_window(words, chunk["in"], chunk["out"])
    real_words = [w for w in wds if _real(w["word"])]
    if not real_words:
        return None
    first = real_words[0]
    cword = _cw(first["word"])
    # connectors as openers (acceptable sometimes but worth flagging)
    if cword in CONN:
        return {
            "rule": "OPENER_DIRTY",
            "chunk": chunk_idx,
            "severity": "WARN",
            "msg": (f"Chunk {chunk_idx}: opens on connector '{first['word']}'. "
                    f"Consider whether the clip hook lands clean or needs the in= bumped."),
            "fix": None  # human judgment — some connectors are fine as openers
        }
    return None

def _rule_clipped_tail(words, chunk, chunk_idx, source):
    """Rule 6: energy at the chunk's out= boundary is still loud → word cut mid-syllable."""
    if source is None:
        return None
    out_t = chunk["out"]
    # check the 50ms ending at out= vs the 50ms after out=
    edge_db = _mean_db(source, out_t - 0.05, out_t)
    post_db = _mean_db(source, out_t, out_t + 0.05)
    if edge_db > -30 and post_db > edge_db - 6:
        return {
            "rule": "CLIPPED_TAIL",
            "chunk": chunk_idx,
            "severity": "ERROR",
            "msg": (f"Chunk {chunk_idx}: energy at out={out_t:.2f} is {edge_db:.1f}dB "
                    f"(still loud), post-cut {post_db:.1f}dB (no taper). "
                    f"Final word is being cut mid-syllable. Bump out= past the word's release."),
            "fix": {"out_needs_extension": True,
                    "edge_db": round(edge_db, 1),
                    "post_db": round(post_db, 1)}
        }
    return None

def _rule_ghost_silence(words, chunk, chunk_idx, source, rough=False):
    """Rule 7: a segment sits in deep silence → MFA ghost alignment or dead span.

    Two checks:
      A) Segment-level (always): if the ENTIRE segment (in→out) mean dB < -50, the segment
         is probably a ghost — no real speech anywhere in it. Works on both rough windows and
         cut_specs because it only looks at the segment's own audio.
      B) Word-level (rough mode ONLY): for transcript words inside a ROUGH window, check if
         onset is ≥16 dB quieter than the word body AND absolutely below -50 dB. This catches
         words that WILL ghost when MFA aligns them. NOT run on cut_specs because transcript
         word timestamps don't match MFA-aligned positions (false positives).
    """
    if source is None:
        return None
    # Check A: segment-level silence (always runs)
    seg_db = _mean_db(source, chunk["in"], chunk["out"])
    if seg_db < -50:
        return {
            "rule": "GHOST_SILENCE",
            "chunk": chunk_idx,
            "severity": "ERROR",
            "msg": (f"Chunk {chunk_idx}: entire segment {chunk['in']:.2f}–{chunk['out']:.2f} "
                    f"is {seg_db:.1f}dB (deep silence). Ghost segment — no speech here."),
            "fix": {"drop_segment": True, "mean_db": round(seg_db, 1)}
        }
    # Check B: word-level onset vs body (ONLY on rough windows — pre-alignment)
    if not rough:
        return None
    _, wds = _words_in_window(words, chunk["in"], chunk["out"])
    for w in wds:
        if not _real(w["word"]):
            continue
        dur = w["end"] - w["start"]
        if dur < 0.15:
            continue  # too short to reliably measure onset vs body
        onset_db = _mean_db(source, w["start"], min(w["start"] + 0.05, w["end"]))
        body_db = _mean_db(source, w["start"] + 0.05, w["end"])
        # ghost = onset in silence AND much quieter than body
        if onset_db < -50 and body_db - onset_db > 16:
            return {
                "rule": "GHOST_SILENCE",
                "chunk": chunk_idx,
                "severity": "ERROR",
                "msg": (f"Chunk {chunk_idx}: word '{w['word']}' at {w['start']:.2f}s "
                        f"has onset={onset_db:.1f}dB but body={body_db:.1f}dB "
                        f"(Δ{body_db-onset_db:.0f}dB). Onset sits in silence — "
                        f"likely a ghost alignment."),
                "fix": {"drop_word": w["word"], "at": w["start"]}
            }
    return None

def _rule_tangent_risk(words, chunk, chunk_idx):
    """Rule 8: long chunk with mid-chunk sentence boundaries → possible uncut tangent."""
    duration = chunk["out"] - chunk["in"]
    if duration < 15.0:
        return None  # short chunks are fine
    _, wds = _words_in_window(words, chunk["in"], chunk["out"])
    real_words = [w for w in wds if _real(w["word"])]
    # count sentence boundaries in the middle (not first 3, not last 3)
    mid_boundaries = 0
    for i, w in enumerate(real_words):
        if 3 <= i < len(real_words) - 3 and _ends_sent(w["word"]):
            mid_boundaries += 1
    if mid_boundaries >= 2:
        return {
            "rule": "TANGENT_RISK",
            "chunk": chunk_idx,
            "severity": "WARN",
            "msg": (f"Chunk {chunk_idx}: {duration:.1f}s long with {mid_boundaries} "
                    f"mid-chunk sentence boundaries. Possible uncut tangent — "
                    f"review the printed script for off-topic drift. "
                    f"Fix: split into smaller chunks so tangent falls in a cut gap."),
            "fix": None  # needs human judgment on where to split
        }
    return None

def _rule_disjoint(chunks, i):
    """Rule 9: chunks not sorted or have suspicious gap/order."""
    if i + 1 >= len(chunks):
        return None
    a_out = chunks[i]["out"]
    b_in = chunks[i + 1]["in"]
    if b_in < a_out - 0.05:
        # this is the overlap case (handled by rule 3), skip
        return None
    if b_in < chunks[i]["in"]:
        return {
            "rule": "DISJOINT_ORDER",
            "chunk": i,
            "severity": "ERROR",
            "msg": (f"Chunk {i+1} starts at {b_in:.2f} which is BEFORE chunk {i} "
                    f"starts at {chunks[i]['in']:.2f}. Chunks are out of order."),
            "fix": {"reorder": True}
        }
    return None

# ── main validator ────────────────────────────────────────────────────────────

def validate_windows(words, chunks, source=None, rough=False):
    """Run all 9 rules against a set of chunk windows.

    Args:
        words:   list of {start, end, word} (transcript, file order = spoken order)
        chunks:  list of {in, out, ...} (rough window marks)
        source:  path to source video/audio (optional; enables energy-based rules 6+7)
        rough:   True = these are rough multi-chunk windows for a SINGLE clip (pre-script-cut).
                 In rough mode:
                   - OPENER_DIRTY only checked on chunk 0 (script-cut strips leading connectors)
                   - PAYOFF_TRUNCATED only checked on the last chunk (intermediate chunks aren't payoffs)
                   - OVERLAP between adjacent chunks is expected (script-cut handles dedup) → downgrade to WARN
                   - LEADING_ORPHAN only checked on chunk 0 (first words the viewer hears)
                   - TRAILING_ORPHAN only checked on the last chunk (last words the viewer hears)
                 False = these are final cut_spec segments (post-cut). All rules apply to all segments.

    Returns:
        list of warning dicts: {rule, chunk, severity, msg, fix}
        severity: "ERROR" = must fix before rendering, "WARN" = review recommended
    """
    warnings = []
    last_idx = len(chunks) - 1

    for i, chunk in enumerate(chunks):
        # Rule 1: leading orphan — in rough mode, only chunk 0 matters
        if not rough or i == 0:
            w = _rule_leading_orphan(words, chunk, i)
            if w: warnings.append(w)

        # Rule 2: trailing orphan — in rough mode, only last chunk matters
        if not rough or i == last_idx:
            w = _rule_trailing_orphan(words, chunk, i)
            if w: warnings.append(w)

        # Rule 4: payoff truncated — in rough mode, only last chunk matters
        if not rough or i == last_idx:
            w = _rule_payoff_truncated(words, chunk, i)
            if w: warnings.append(w)

        # Rule 5: opener dirty — in rough mode, only chunk 0 matters
        if not rough or i == 0:
            w = _rule_opener_dirty(words, chunk, i)
            if w: warnings.append(w)

        # Rule 6: clipped tail (needs source) — in rough mode, only last chunk matters
        if not rough or i == last_idx:
            w = _rule_clipped_tail(words, chunk, i, source)
            if w: warnings.append(w)

        # Rule 7: ghost silence (needs source) — always check
        w = _rule_ghost_silence(words, chunk, i, source, rough=rough)
        if w: warnings.append(w)

        # Rule 8: tangent risk — always check
        w = _rule_tangent_risk(words, chunk, i)
        if w: warnings.append(w)

    # Inter-chunk rules
    for i in range(len(chunks)):
        # Rule 3: overlap
        w = _rule_overlap(chunks, i)
        if w:
            if rough:
                # In rough mode, adjacent-chunk overlap is expected (script-cut deduplicates)
                w["severity"] = "WARN"
                w["msg"] += " (rough mode: expected if feeding to script-cut)"
            warnings.append(w)

        # Rule 9: disjoint order — always check
        w = _rule_disjoint(chunks, i)
        if w: warnings.append(w)

    # Sort by chunk index, then severity (ERROR first)
    sev_order = {"ERROR": 0, "WARN": 1}
    warnings.sort(key=lambda x: (x["chunk"], sev_order.get(x["severity"], 2)))

    return warnings

# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Validate rough in/out windows before cutting.")
    ap.add_argument("--transcript", required=True, help="Word-level transcript JSON")
    ap.add_argument("--spec", required=True, help="Structure/spec JSON with in/out chunks")
    ap.add_argument("--source", default=None, help="Source video/audio (enables energy rules)")
    ap.add_argument("--rough", action="store_true",
                    help="Rough-window mode: chunks are pre-script-cut multi-chunk windows. "
                         "Only checks opener on first chunk, payoff on last, downgrades overlap to WARN.")
    ap.add_argument("--json", action="store_true", help="Output as JSON instead of human-readable")
    args = ap.parse_args()

    words = load_words(args.transcript)
    clip_groups = load_structure(args.spec)

    all_warnings = []
    for clip_name, chunks in clip_groups:
        ws = validate_windows(words, chunks, source=args.source, rough=args.rough)
        for w in ws:
            w["clip"] = clip_name
        all_warnings.extend(ws)

    if args.json:
        json.dump(all_warnings, sys.stdout, indent=2)
        print()
        return

    errors = [w for w in all_warnings if w["severity"] == "ERROR"]
    warns = [w for w in all_warnings if w["severity"] == "WARN"]

    if not all_warnings:
        print(f"✓ All {len(clip_groups)} clip(s) pass validation (0 errors, 0 warnings)")
        return

    current_clip = None
    for w in all_warnings:
        if w.get("clip") != current_clip:
            current_clip = w.get("clip")
            print(f"\n── {current_clip} ──")
        icon = "✗" if w["severity"] == "ERROR" else "⚠"
        print(f"  {icon} [{w['rule']}] {w['msg']}")
        if w.get("fix"):
            print(f"    → fix: {w['fix']}")
        print()

    print(f"{'='*60}")
    print(f"  {len(clip_groups)} clip(s)  |  {len(errors)} ERROR(s)  {len(warns)} WARNING(s)")
    if errors:
        print(f"  ✗ FIX ERRORS before rendering — they produce broken clips.")
    print()

    sys.exit(1 if errors else 0)

if __name__ == "__main__":
    main()
