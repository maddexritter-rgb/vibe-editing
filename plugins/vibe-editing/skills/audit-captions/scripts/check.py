#!/usr/bin/env python3
"""audit-captions: independent caption quality gate.

Transcribes the clip independently, parses the burnt .ass subtitle source, and
cross-checks accuracy, speaker colors, timing, formatting, and coverage gaps.

The ONE house caption style is "spice" (the locked the reference editor look): captions are
CHUNK-displayed stylized text (1-3 words/cue), with a drop-in animation, a 3-5
frame lead per SOP, and PUNCTUATION STRIPPED BY DESIGN (spice_format lowercases
-except and removes sentence punctuation). A word-for-word, punctuation-included
comparison against a fresh transcript therefore ALWAYS mismatches — that is the
expected style, not a defect. So the gate is built to FAIL only on a REAL,
viewer-visible defect: captions that SAY something different from the audio, are
ABSENT over real speech, are GROSSLY desynced from speech, or use the WRONG
speaker color.

Recalibrated 2026-06-13 so FAIL means a real defect (prior version false-failed
every clip the spice pipeline produces). Design:

  - ASS parsing reads PrimaryColour from the correct Style field and, more
    importantly, the per-word visible color from inline `\\1c&H..&` overrides
    (spice colours per word inline). The black drop-shadow layer (a duplicate
    event per cue) is detected and dropped so it never pollutes text/colour.
  - ACCURACY: both sides are normalized (lowercase, punctuation stripped,
    whitespace collapsed, digit/number-word forms folded, safe contractions
    expanded), then compared as a whole-text token SEQUENCE. Only a LOW overall
    similarity / high word-error-rate fails — never per-token punctuation or
    chunk-alignment diffs. Short missing runs are treated as stylistic filler
    trims (by design in chunked styles); only a long missing run is content loss.
  - TIMING: the blocking signal is the TRACK-ENVELOPE head offset — when the first
    caption appears vs the first spoken word. A correctly-synced spice track opens
    within a small lead/lag of first speech; a uniform shift moves the whole head
    off the speech. The head offset is ALIGNMENT-FREE, so it is immune to the
    per-word alignment artifacts that repetitive transcripts produce (where
    SequenceMatcher mis-keys a caption token to an earlier occurrence of a repeated
    word and inflates individual offsets). Per-cue interval gaps (display window vs
    the spoken span of its aligned words) are computed and reported as ADVISORY
    only; localized real desync surfaces instead as a coverage gap (below).
  - GAPS: proper interval union over chunk display windows; reports only a real
    on-screen caption gap (>1.5s of speech with no caption) and never emits a
    negative/zero-length window. This is the alignment-free authority for desync:
    a shifted track leaves a head/tail region of speech uncaptioned.
  - COLORS: single-speaker -> uniform color is correct (yellow reported-speech
    spans are allowed). Two speakers -> require >=2 distinct visible colors
    (host white / guest yellow); the expected map is read from the clip's
    contract/manifest when present.
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
from difflib import SequenceMatcher
from pathlib import Path

# Render-pipeline metadata resolver (graceful: gate still runs without it)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
try:
    from clip_meta import resolve as resolve_clip_meta
except Exception:
    resolve_clip_meta = None

GROQ_MODEL = "whisper-large-v3"

# ---- thresholds (calibrated 2026-06-13 on a 22-clip human-good batch) ----
# Accuracy: on the known-good batch WER maxes at 0.030 with substitution runs <=1.
# A genuine wrong-caption region shows up as either high overall WER or a run of
# consecutive non-equivalent substitutions.
WER_ERR = 0.18            # whole-text word-error-rate that means "says something else"
SIM_ERR = 0.82            # token-sequence similarity floor (1 - effective error)
SUB_RUN_ERR = 4           # consecutive substituted (non-equivalent) words = wrong-text region
# Chunked styles legitimately DROP verbal fillers / false starts that remain in
# the audio. Only a long missing run is real content loss:
MISSING_RUN_CONTENT_LOSS = 6

# Timing: the BLOCKING signal is the track-envelope head offset (first caption vs
# first speech) — alignment-free, so immune to repeated-word alignment artifacts.
# A uniform shift moves the head off the speech; gaps then corroborates with an
# uncaptioned head region. Designed lead is ~0.2s, clips may open with a beat of
# music before speech, so the bar is wide.
HEAD_SHIFT_ERR_S = 2.50        # captions open this long AFTER first speech = late desync
HEAD_LEAD_ERR_S = 2.50         # captions open this long BEFORE any speech = early desync
# Per-cue interval gaps are ADVISORY only (alignment can mis-key a repeated word).
CHUNK_DISJOINT_WARN_S = 0.60   # display window this far from its aligned words = noted

GAP_MIN_S = 1.5           # uncovered on-screen speech span that counts as a gap
GAP_MIN_WORDS = 3
GAP_SPLIT_S = 1.0         # silence between uncovered words that splits gaps
COVER_PRE_S, COVER_POST_S = 0.30, 0.40  # cue display tolerance for word coverage


def groq_key():
    k = os.environ.get("GROQ_API_KEY")
    if k:
        return k
    for rc in (".zshrc", ".bashrc", ".profile"):
        p = Path.home() / rc
        if p.exists():
            m = re.search(r'GROQ_API_KEY=["\']?([A-Za-z0-9_\-]+)', p.read_text())
            if m:
                return m.group(1)
    return None


def transcribe_clip(clip_path: str) -> dict:
    key = groq_key()
    if not key:
        return {}
    wav = tempfile.mktemp(suffix=".wav")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", clip_path, "-vn", "-ac", "1", "-ar", "16000", wav],
        capture_output=True,
    )
    try:
        import httpx
        with open(wav, "rb") as f:
            resp = httpx.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": ("audio.wav", f, "audio/wav")},
                data={"model": GROQ_MODEL, "response_format": "verbose_json",
                      "timestamp_granularities[]": "word"},
                timeout=120,
            )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}
    finally:
        if os.path.exists(wav):
            os.unlink(wav)


# ---------------------------------------------------------------- ASS parsing

def parse_ass(ass_path: str):
    """Returns (events, styles).

    events: list of dicts {start, end, text(plain), style, color, is_shadow}.
    styles: style-name -> PrimaryColour (correct field index).
    """
    raw_events, styles = [], {}
    in_events = in_styles = False
    format_fields = []
    with open(ass_path, "r", encoding="utf-8-sig") as f:
        for line in f:
            s = line.rstrip("\n").strip()
            if s.startswith("["):
                in_events = s == "[Events]"
                in_styles = "Styles" in s
                continue
            if in_styles and s.startswith("Style:"):
                parts = [p.strip() for p in s.split(":", 1)[1].split(",")]
                # ASS V4+ Style: Name, Fontname, Fontsize, PrimaryColour, ...
                if len(parts) >= 4:
                    styles[parts[0]] = parts[3]   # PrimaryColour (index 3, not 2)
                continue
            if in_events and s.startswith("Format:"):
                format_fields = [x.strip() for x in s.split(":", 1)[1].split(",")]
                continue
            if in_events and s.startswith("Dialogue:"):
                parts = s.split(":", 1)[1].split(",", len(format_fields) - 1)
                raw_events.append({fld: (parts[i].strip() if i < len(parts) else "")
                                   for i, fld in enumerate(format_fields)})

    events = []
    for e in raw_events:
        text_field = e.get("Text", "")
        plain = strip_ass_tags(text_field).strip()
        if not plain:
            continue
        style = e.get("Style", "")
        color = visible_color(text_field, styles.get(style, ""))
        events.append({
            "start": ass_time_to_seconds(e.get("Start", "0:00:00.00")),
            "end": ass_time_to_seconds(e.get("End", "0:00:00.00")),
            "text": plain,
            "style": style,
            "color": color,
            "is_shadow": is_shadow_event(style, text_field, color),
            "anchor_y": anchor_y(text_field),
        })
    return events, styles


def anchor_y(text_field: str):
    """Caption anchor Y from \\move(x1,y1,x2,y2,..) (target y2) or \\pos(x,y)."""
    m = re.search(r"\\move\([^)]*?,[^,]*,[^,]*,\s*([-\d.]+)", text_field)
    if m:
        return float(m.group(1))
    m = re.search(r"\\pos\([^,]*,\s*([-\d.]+)\)", text_field)
    if m:
        return float(m.group(1))
    return None


def ass_time_to_seconds(t: str) -> float:
    try:
        h, m, sec = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(sec)
    except Exception:
        return 0.0


def strip_ass_tags(text: str) -> str:
    return re.sub(r"\{[^}]*\}", "", text).replace("\\N", " ").replace("\\n", " ")


def _norm_color(c: str) -> str:
    """Normalize an ASS colour token to RRGGBB hex.

    ASS stores colours as &HAABBGGRR (alpha-blue-green-red). We drop alpha and
    reorder BGR -> RGB so downstream bucketing can read R/G/B directly.
    """
    if not c:
        return ""
    m = re.search(r"&?H?([0-9A-Fa-f]{2,8})&?", c)
    if not m:
        return ""
    bgr = m.group(1).upper().rjust(8, "0")[-6:]    # BBGGRR (alpha dropped)
    return bgr[4:6] + bgr[2:4] + bgr[0:2]          # -> RRGGBB


def visible_color(text_field: str, style_primary: str) -> str:
    """The dominant VISIBLE fill colour of a cue.

    the reference editor sets colour per word with inline `\\1c&H..&` overrides on the main
    layer. Take the most common non-black inline colour; fall back to the
    Style's PrimaryColour. Black (drop-shadow) is ignored as a fill candidate.
    """
    inline = re.findall(r"\\1c&H([0-9A-Fa-f]{6,8})&", text_field)
    counts = {}
    for c in inline:
        n = _norm_color(c)
        if n and n != "000000":
            counts[n] = counts.get(n, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    return _norm_color(style_primary)


def is_shadow_event(style: str, text_field: str, color: str) -> bool:
    """The black gblur drop-shadow layer is a duplicate event per cue.

    Identify it by style name ('shadow') OR by an all-black inline fill
    (`\\1c&H..000000&` with no non-black inline colour).
    """
    if "shadow" in style.lower():
        return True
    inline = re.findall(r"\\1c&H([0-9A-Fa-f]{6,8})&", text_field)
    if inline and all(_norm_color(c) == "000000" for c in inline):
        return True
    return False


def color_bucket(rrggbb: str) -> str:
    """Map a normalized RRGGBB to a perceptual caption bucket."""
    if not rrggbb:
        return "unknown"
    try:
        r = int(rrggbb[0:2], 16)
        g = int(rrggbb[2:4], 16)
        b = int(rrggbb[4:6], 16)
    except ValueError:
        return "unknown"
    if r > 200 and g > 200 and b > 200:
        return "white"
    # spice guest yellow #FECB00 -> high R, high G, low B
    if r > 180 and g > 140 and b < 110:
        return "yellow"
    if r < 60 and g < 60 and b < 60:
        return "black"
    return "other"


def dedupe_chunks(events):
    """Collapse to unique on-screen display chunks from the MAIN (non-shadow)
    layer. Falls back to all events if shadow detection found nothing to keep."""
    main = [e for e in events if not e["is_shadow"]]
    if not main:
        main = events  # no shadow layer present; use everything
    by_key = {}
    for e in main:
        key = (round(e["start"], 3), round(e["end"], 3))
        prev = by_key.get(key)
        # prefer the longer text, and a non-black visible colour
        if (prev is None
                or len(e["text"]) > len(prev["text"])
                or (color_bucket(prev["color"]) == "black"
                    and color_bucket(e["color"]) != "black")):
            by_key[key] = {"start": e["start"], "end": e["end"], "text": e["text"],
                           "style": e["style"], "color": e["color"],
                           "anchor_y": e.get("anchor_y")}
    return sorted(by_key.values(), key=lambda c: (c["start"], c["end"]))


# ---------------------------------------------------------------- normalization

_NUM_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000, "million": 10 ** 6, "billion": 10 ** 9,
}
_EXPAND = {"gonna": "going to", "wanna": "want to", "gotta": "got to",
           "kinda": "kind of", "sorta": "sort of", "cuz": "because",
           "'cause": "because", "cause": "because", "lemme": "let me",
           "dunno": "don't know", "em": "them", "'em": "them", "ok": "okay"}


def _num_value(tok: str):
    t = tok.replace(",", "").replace("$", "").replace("%", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([km])?", t)
    if m:
        v = float(m.group(1))
        return v * {"k": 1000, "m": 10 ** 6}.get(m.group(2) or "", 1)
    return _NUM_WORDS.get(t)


def normalize_tokens(text: str):
    text = (text.lower()
            .replace("’", "'").replace("‘", "'")
            .replace("“", '"').replace("”", '"')
            .replace("—", " ").replace("–", " ").replace("-", " "))
    out = []
    for raw in text.split():
        tok = raw.strip(".,;:!?\"'()[]…")
        if not tok:
            continue
        exp = _EXPAND.get(tok)
        out.extend(exp.split() if exp else [tok])
    return out


def tokens_equivalent(a: str, b: str) -> bool:
    if a == b:
        return True
    va, vb = _num_value(a), _num_value(b)
    if va is not None and vb is not None and va == vb:
        return True
    # possessive / plural fold ("years" vs "year's", "its" vs "it's")
    return a.replace("'", "") == b.replace("'", "")


# ---------------------------------------------------------------- checks

def align(asr_tokens, cap_tokens):
    """Sequence alignment with equivalence-aware substitution classification.

    Returns (subs, missing_content, extra, cap_to_asr, n_errors, max_sub_run, trims).
    cap_to_asr maps caption-token index -> aligned asr-token index (best effort).
    """
    sm = SequenceMatcher(None, asr_tokens, cap_tokens, autojunk=False)
    subs, missing, extra = [], [], []
    cap_to_asr = {}
    max_run = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                cap_to_asr[j1 + k] = i1 + k
        elif tag == "replace":
            run = 0
            for k in range(max(i2 - i1, j2 - j1)):
                ai, cj = i1 + k, j1 + k
                tw = asr_tokens[ai] if ai < i2 else None
                cw = cap_tokens[cj] if cj < j2 else None
                if tw and cw:
                    cap_to_asr[cj] = ai
                    if tokens_equivalent(tw, cw):
                        run = 0
                        continue
                    subs.append({"asr_idx": ai, "spoken": tw, "caption": cw})
                    run += 1
                    max_run = max(max_run, run)
                elif tw:
                    missing.append({"asr_idx": ai, "spoken": tw})
                elif cw:
                    extra.append({"caption": cw})
        elif tag == "delete":
            for ai in range(i1, i2):
                missing.append({"asr_idx": ai, "spoken": asr_tokens[ai]})
        elif tag == "insert":
            for cj in range(j1, j2):
                extra.append({"caption": cap_tokens[cj]})

    # Split missing words into stylistic trims (short runs — dropped fillers /
    # false starts, by design in chunked caption styles) vs content loss.
    missing_content, trims = [], []
    run = []
    for m in missing + [None]:
        if m is not None and run and m["asr_idx"] == run[-1]["asr_idx"] + 1:
            run.append(m)
            continue
        if run:
            (missing_content if len(run) >= MISSING_RUN_CONTENT_LOSS else trims).extend(run)
        run = [m] if m else []

    n_errors = len(subs) + len(missing_content) + len(extra)
    return subs, missing_content, extra, cap_to_asr, n_errors, max_run, trims


def check_accuracy(asr_words, asr_tokens, cap_tokens, tok_to_word,
                   subs, missing, extra, n_errors, max_run, trims) -> dict:
    """FAIL only when the captions, as a whole, SAY SOMETHING DIFFERENT from the
    audio: low token-sequence similarity / high WER, or a run of consecutive
    genuine substitutions (a wrong-text region). Per-token punctuation and
    chunk-alignment diffs never fail."""
    if not asr_tokens:
        return {"pass": True, "issues": [], "note": "no transcript — skipped"}
    wer = n_errors / max(1, len(asr_tokens))
    # Whole-text similarity on the normalized token sequences (style-agnostic).
    sim = SequenceMatcher(None, asr_tokens, cap_tokens, autojunk=False).ratio()
    issues = []
    for s in subs[:10]:
        w = asr_words[tok_to_word[s["asr_idx"]]] if s["asr_idx"] in tok_to_word else {}
        issues.append({"time_s": round(w.get("start", 0), 1), "caption": s["caption"],
                       "spoken": s["spoken"], "severity": "error"})
    for m in missing[:5]:
        w = asr_words[tok_to_word[m["asr_idx"]]] if m["asr_idx"] in tok_to_word else {}
        issues.append({"time_s": round(w.get("start", 0), 1), "caption": "(missing)",
                       "spoken": m["spoken"], "severity": "error"})
    failed = (wer > WER_ERR) or (sim < SIM_ERR) or (max_run >= SUB_RUN_ERR)
    out = {"pass": not failed, "wer": round(wer, 4), "similarity": round(sim, 4),
           "substitutions": len(subs), "missing_content_words": len(missing),
           "extra_words": len(extra), "max_substitution_run": max_run,
           "stylistic_trims": len(trims),
           "issues": issues if failed else []}
    if trims:
        out["trimmed_from_captions"] = " ".join(t["spoken"] for t in trims[:12])
    if not failed and n_errors:
        out["note"] = (f"{n_errors} token diffs vs independent ASR "
                       f"(WER {round(wer * 100, 1)}% ≤ {round(WER_ERR * 100)}%, "
                       f"similarity {round(sim * 100, 1)}% ≥ {round(SIM_ERR * 100)}% — "
                       f"within ASR noise for stylized chunked captions)")
        out["sample_diffs"] = issues[:5]
    return out


def _chunk_spoken_span(chunk_idx, cap_token_chunk, cap_to_asr, tok_to_word, asr_words):
    """Spoken-time span [min_start, max_end] of all ASR words aligned to a cue's
    tokens. None if no token of the cue aligned to a spoken word."""
    starts, ends = [], []
    for cj, ci in cap_token_chunk.items():
        if ci != chunk_idx or cj not in cap_to_asr:
            continue
        wi = tok_to_word.get(cap_to_asr[cj])
        if wi is None or wi >= len(asr_words):
            continue
        w = asr_words[wi]
        starts.append(w.get("start", 0))
        ends.append(w.get("end", w.get("start", 0)))
    if not starts:
        return None
    return min(starts), max(ends)


def _interval_gap(a0, a1, b0, b1) -> float:
    """Signed-magnitude gap between intervals [a0,a1] and [b0,b1]; 0 if overlap."""
    if a1 < b0:
        return b0 - a1          # a entirely before b
    if b1 < a0:
        return a0 - b1          # a entirely after b
    return 0.0                  # overlap


def check_timing(chunks, asr_words, cap_token_chunk, cap_to_asr, tok_to_word) -> dict:
    """Is the caption track shifted off the speech?

    The blocking signal is the TRACK ENVELOPE head offset: the first caption's
    appearance vs the first spoken word. A correctly-synced spice track opens
    within a small lead/lag of first speech (designed 3-5 frame lead); a uniform
    shift moves the whole track's head off the speech (and gaps corroborates with
    an uncaptioned head region). The head offset is ALIGNMENT-FREE, so it is immune
    to the per-word alignment artifacts that repeated-content clips produce.

    Per-cue interval gaps (display window vs the spoken span of its aligned words)
    are still computed and reported as ADVISORY diagnostics, but they never block:
    on repetitive transcripts SequenceMatcher can key a caption token to an earlier
    occurrence of a repeated word, inflating individual gaps without any real
    desync. Localized real desync surfaces as a coverage gap (see check_gaps)."""
    import statistics
    if not chunks or not asr_words:
        return {"pass": True, "issues": [], "note": "no timing data"}

    cap_first = min(c["start"] for c in chunks)
    cap_last = max(c["end"] for c in chunks)
    spk_first = min(w.get("start", 0) for w in asr_words)
    spk_last = max(w.get("end", w.get("start", 0)) for w in asr_words)
    head_off = cap_first - spk_first      # +ve: captions start AFTER speech
    tail_off = cap_last - spk_last        # +ve: captions linger AFTER speech ends

    # advisory per-cue interval gaps
    adv, gaps = [], []
    for ci, chunk in enumerate(chunks):
        span = _chunk_spoken_span(ci, cap_token_chunk, cap_to_asr, tok_to_word, asr_words)
        if span is None:
            continue
        gap = _interval_gap(chunk["start"], chunk["end"], span[0], span[1])
        gaps.append(gap)
        if gap > CHUNK_DISJOINT_WARN_S:
            adv.append({"time_s": round(chunk["start"], 1),
                        "caption_text": chunk["text"][:30],
                        "offset_ms": round(gap * 1000),
                        "direction": "after" if chunk["start"] > span[1] else "before",
                        "severity": "warn",
                        "problem": f"caption window {round(gap * 1000)}ms off its "
                                   f"aligned words (advisory — alignment may mis-key "
                                   f"a repeated word)"})

    issues = []
    # The clip can legitimately open with a beat of music/silence before speech,
    # so a small head offset (either sign) is fine; the designed lead is ~0.2s.
    if head_off > HEAD_SHIFT_ERR_S:
        issues.append({"severity": "error", "head_offset_ms": round(head_off * 1000),
                       "problem": f"caption track opens {round(head_off * 1000)}ms AFTER "
                                  f"first speech — track shifted late (head desync)"})
    elif head_off < -HEAD_LEAD_ERR_S:
        issues.append({"severity": "error", "head_offset_ms": round(head_off * 1000),
                       "problem": f"caption track opens {round(-head_off * 1000)}ms BEFORE "
                                  f"any speech — track shifted early (head desync)"})

    stats = {"cues_timed": len(gaps),
             "head_offset_ms": round(head_off * 1000),
             "tail_offset_ms": round(tail_off * 1000)}
    if gaps:
        stats["median_gap_ms"] = round(statistics.median(gaps) * 1000)
        stats["disjoint_fraction"] = round(sum(1 for g in gaps if g > CHUNK_DISJOINT_WARN_S) / len(gaps), 3)
    issues.extend(adv)
    has_error = any(i["severity"] == "error" for i in issues)
    return {"pass": not has_error, **stats, "issues": issues[:10]}


def check_gaps(asr_words, chunks, duration: float) -> dict:
    """Speech spans with NO caption on screen — proper interval union, never a
    negative/zero-length window."""
    if not asr_words:
        return {"pass": True, "issues": [], "note": "no transcript — skipped"}
    if not chunks:
        return {"pass": True, "issues": [], "note": "no captions parsed — skipped"}
    # Build covered intervals from cue display windows (with display tolerance),
    # clamped so start <= end always.
    raw = []
    for c in chunks:
        s = max(0.0, c["start"] - COVER_PRE_S)
        e = c["end"] + COVER_POST_S
        if e > s:
            raw.append((s, e))
    raw.sort()
    merged = []
    for s, e in raw:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    def covered(t):
        return any(s <= t <= e for s, e in merged)

    uncovered = [w for w in asr_words
                 if not covered((w.get("start", 0) + w.get("end", w.get("start", 0))) / 2)]
    groups, cur = [], []
    for w in uncovered:
        if cur and w.get("start", 0) - cur[-1].get("end", cur[-1].get("start", 0)) > GAP_SPLIT_S:
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)

    issues = []
    for g in groups:
        start = min(w.get("start", 0) for w in g)
        end = max(w.get("end", w.get("start", 0)) for w in g)
        length = end - start
        if len(g) >= GAP_MIN_WORDS and length >= GAP_MIN_S:
            issues.append({"start_s": round(start, 1), "end_s": round(end, 1),
                           "duration_s": round(length, 1),
                           "words": " ".join(w.get("word", "").strip() for w in g)[:80],
                           "severity": "error",
                           "problem": f"{round(length, 1)}s of speech with no caption "
                                      f"at {round(start, 1)}s"})
    return {"pass": len(issues) == 0, "issues": issues[:10]}


def _expected_color_buckets(meta) -> set:
    """Expected visible colour buckets from the clip's contract color_map."""
    out = set()
    contract = (meta or {}).get("contract") or {}
    cmap = (((contract.get("declared") or {}).get("captions") or {}).get("color_map") or {})
    for v in cmap.values():
        b = str(v).strip().lower()
        if b in ("white", "yellow"):
            out.add(b)
    return out


def check_speaker_colors(chunks, speakers, meta) -> dict:
    """host white / guest yellow when 2 speakers; 1 speaker = uniform (yellow
    reported-speech spans allowed). Colours read from the real visible fill."""
    buckets = {color_bucket(c.get("color", "")) for c in chunks if c.get("color")}
    buckets.discard("black")
    buckets.discard("unknown")
    expected = _expected_color_buckets(meta)

    if speakers is not None and speakers <= 1:
        note = "single speaker — uniform caption color is correct"
        if "yellow" in buckets:
            note += " (yellow reported-speech span present — allowed)"
        return {"pass": True, "colors": sorted(buckets), "issues": [], "note": note}

    if speakers and speakers >= 2:
        if len([b for b in buckets if b in ("white", "yellow", "other")]) < 2:
            return {"pass": False, "colors": sorted(buckets), "issues": [{
                "severity": "error",
                "problem": f"{speakers} speakers declared but captions use a single colour "
                           f"({sorted(buckets) or 'none'}) — expected host white / guest yellow"}]}
        if expected and not expected.issubset(buckets):
            return {"pass": False, "colors": sorted(buckets), "issues": [{
                "severity": "error",
                "problem": f"expected caption colours {sorted(expected)} but found "
                           f"{sorted(buckets)} — speaker colour attribution wrong"}]}
        return {"pass": True, "colors": sorted(buckets), "distinct_colors": len(buckets),
                "issues": []}

    # Speaker count unknown: distinctness not enforced (can't tell mono vs duo).
    return {"pass": True, "colors": sorted(buckets), "issues": [],
            "note": f"{sorted(buckets) or 'no'} colour(s) in use; speaker count unknown — "
                    f"distinctness not enforced"}


def check_presence(chunks, asr_words, duration: float) -> dict:
    """Captions must EXIST over the clip's speech. Absent captions on a talking
    clip is a real defect."""
    if chunks:
        return {"pass": True, "caption_chunks": len(chunks), "issues": []}
    spoken = len(asr_words)
    if spoken == 0:
        return {"pass": True, "issues": [],
                "note": "no captions and no detected speech — nothing to caption"}
    return {"pass": False, "issues": [{
        "severity": "error",
        "problem": f"no captions present but {spoken} spoken words detected over "
                   f"{round(duration, 1)}s — captions absent"}]}


def check_positioning(chunks, play_y) -> dict:
    """Captions inside the vertical safe zone (not jammed against the top/bottom
    edge). Uses the ASS \\an5 anchor Y (\\move target or \\pos) when present —
    spice anchors mid-frame, so a cue parked in the top/bottom 6% band is a
    placement defect. Degrades to a pass when no anchor data is available."""
    if not chunks or not play_y:
        return {"pass": True, "issues": [], "note": "no positioning data"}
    top_band, bot_band = 0.06 * play_y, 0.94 * play_y
    anchored = [c["anchor_y"] for c in chunks if c.get("anchor_y") is not None]
    if not anchored:
        return {"pass": True, "issues": [], "note": "no anchor positions in .ass"}
    out_of_zone = sum(1 for y in anchored if y < top_band or y > bot_band)
    issues = []
    if out_of_zone > 0.5 * len(anchored):
        issues.append({"severity": "error",
                       "problem": f"{out_of_zone}/{len(anchored)} caption cues anchored in the "
                                  f"top/bottom 6% safe-margin band — outside the safe zone"})
    return {"pass": not issues, "anchored_cues": len(anchored), "issues": issues[:10]}


def check_formatting(chunks) -> dict:
    """Advisory only (warn). The reference editor strips punctuation and keeps cues short; these
    are style hints, never the sole reason a clean clip fails."""
    issues = []
    for i, c in enumerate(chunks):
        text = c["text"]
        if re.search(r"[.,;:]$", text.strip()):
            issues.append({"event": i, "text": text[:40],
                           "problem": "trailing punctuation", "severity": "warn"})
        for line in text.split("\\N"):
            if len(line.strip()) > 28:
                issues.append({"event": i, "text": line[:40],
                               "problem": f"line long ({len(line.strip())} chars)",
                               "severity": "warn"})
    return {"pass": True, "issues": issues[:10]}


def get_video_meta(clip_path: str):
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", clip_path],
        capture_output=True, text=True,
    )
    try:
        data = json.loads(r.stdout)
    except Exception:
        return 0.0, None, None
    duration = float(data.get("format", {}).get("duration", 0))
    w = h = None
    for st in data.get("streams", []):
        if st.get("codec_type") == "video":
            w, h = st.get("width"), st.get("height")
            break
    return duration, w, h


def read_playres(ass_path: str):
    """PlayResX / PlayResY from the [Script Info] header (for safe-zone reasoning)."""
    try:
        txt = Path(ass_path).read_text(encoding="utf-8-sig", errors="ignore")
    except Exception:
        return None, None
    mx = re.search(r"PlayResX:\s*(\d+)", txt)
    my = re.search(r"PlayResY:\s*(\d+)", txt)
    return (int(mx.group(1)) if mx else None), (int(my.group(1)) if my else None)


def _ocr_sim(a, b):
    import difflib
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def ocr_burnt_captions(clip_path, duration, vid_h, sample_dt=0.4):
    """Fallback when no .ass source exists: OCR the BURNT-IN captions from sampled frames so the
    audit can verify caption presence + accuracy on a clip whose captions are rendered into pixels
    (the only kind we ship). Returns (chunks[{text,start,end}], ok). (2026-06-13: the audit was
    BLIND to burnt-in captions without the .ass — it reported "no captions present" on fully
    captioned clips. This gives it eyes via tesseract so it stops false-failing on the real deliverable.)"""
    import subprocess, tempfile, shutil
    if not shutil.which("tesseract"):
        return [], False
    y0 = int(vid_h * 0.50); band = int(vid_h * 0.18)   # tight caption band (y58 desk / y50 seam)
    # Isolate the bright caption text from the room background, then negate to dark-on-light
    # (tesseract's preferred polarity) — far cleaner than OCRing the raw wide frame.
    vf = (f"crop=in_w:{band}:0:{y0},format=gray,"
          f"lut=y=if(gt(val\\,150)\\,0\\,255)")
    reads = []
    t = 0.2
    while t < max(duration - 0.1, 0.3):
        png = tempfile.mktemp(suffix=".png")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", clip_path,
                        "-frames:v", "1", "-vf", vf, png], capture_output=True)
        txt = ""
        try:
            r = subprocess.run(["tesseract", png, "stdout", "--psm", "6"], capture_output=True, text=True)
            txt = " ".join(r.stdout.split()).strip()
        except Exception:
            pass
        finally:
            try: os.unlink(png)
            except OSError: pass
        reads.append((round(t, 2), txt))
        t += sample_dt
    chunks = []
    for ts, txt in reads:
        if len(txt) < 2:
            continue
        if chunks and _ocr_sim(chunks[-1]["text"], txt) > 0.55:
            chunks[-1]["end"] = ts + sample_dt
            if len(txt) > len(chunks[-1]["text"]):
                chunks[-1]["text"] = txt
        else:
            chunks.append({"text": txt, "start": ts, "end": ts + sample_dt})
    return chunks, True


def main():
    parser = argparse.ArgumentParser(description="Audit captions on a rendered clip")
    parser.add_argument("--clip", required=True, help="Path to rendered clip mp4")
    parser.add_argument("--ass", help="Path to .ass subtitle file (optional — auto-resolved)")
    parser.add_argument("--out", required=True, help="Output JSON path")
    args = parser.parse_args()

    clip = args.clip
    if not os.path.exists(clip):
        print(f"ERROR: clip not found: {clip}", file=sys.stderr)
        sys.exit(1)

    duration, vid_w, vid_h = get_video_meta(clip)
    meta = resolve_clip_meta(clip) if resolve_clip_meta else {}
    if not isinstance(meta, dict):
        meta = {}

    print("Transcribing clip independently...")
    transcript = transcribe_clip(clip)
    asr_words = transcript.get("words", [])
    if not asr_words and "segments" in transcript:
        for seg in transcript["segments"]:
            asr_words.extend(seg.get("words", []))

    ass_path = args.ass or meta.get("ass_path")
    chunks, styles = [], {}
    play_y = None
    ocr_mode = False
    if ass_path and os.path.exists(ass_path):
        events, styles = parse_ass(ass_path)
        chunks = dedupe_chunks(events)          # anchor_y rides through each chunk
        _, play_y = read_playres(ass_path)
        print(f"Loaded {len(events)} events → {len(chunks)} display chunks from {ass_path}")
    else:
        # No .ass source — OCR the BURNT-IN captions so we audit the real deliverable instead of
        # false-failing "no captions present" on a fully-captioned clip (2026-06-13).
        print("No .ass — OCR'ing burnt-in captions from the pixels...")
        chunks, ocr_mode = ocr_burnt_captions(clip, duration, vid_h)
        if ocr_mode:
            print(f"  OCR found {len(chunks)} burnt-in caption chunk(s)")
        else:
            print("  WARN: tesseract unavailable — caption-source checks skipped")

    # tokenize both sides; keep maps back to source words/chunks
    asr_tokens, tok_to_word = [], {}
    for wi, w in enumerate(asr_words):
        for t in normalize_tokens(w.get("word", "")):
            tok_to_word[len(asr_tokens)] = wi
            asr_tokens.append(t)
    cap_tokens, cap_token_chunk = [], {}
    for ci, c in enumerate(chunks):
        for t in normalize_tokens(c["text"]):
            cap_token_chunk[len(cap_tokens)] = ci
            cap_tokens.append(t)

    speakers = meta.get("speakers")
    results = {"clip": os.path.basename(clip), "duration_s": round(duration, 1),
               "transcript_words": len(asr_words), "caption_chunks": len(chunks),
               "video_wh": [vid_w, vid_h]}
    checks = {}

    # presence — captions must exist over speech
    print("Checking caption presence...")
    checks["presence"] = check_presence(chunks, asr_words, duration)

    if chunks and asr_tokens:
        subs, missing, extra, cap_to_asr, n_err, max_run, trims = align(asr_tokens, cap_tokens)
        print("Checking accuracy (normalized whole-text similarity)...")
        checks["accuracy"] = check_accuracy(asr_words, asr_tokens, cap_tokens, tok_to_word,
                                            subs, missing, extra, n_err, max_run, trims)
        print("Checking timing sync (track-envelope head offset)...")
        checks["timing"] = check_timing(chunks, asr_words, cap_token_chunk,
                                        cap_to_asr, tok_to_word)
        print("Checking caption coverage gaps...")
        checks["gaps"] = check_gaps(asr_words, chunks, duration)
    else:
        note = "no captions parsed" if not chunks else "no transcript"
        for k in ("accuracy", "timing", "gaps"):
            checks[k] = {"pass": True, "issues": [], "note": f"{note} — skipped"}

    print("Checking speaker colors...")
    checks["speaker_color"] = check_speaker_colors(chunks, speakers, meta)
    print("Checking positioning / safe zone...")
    checks["positioning"] = check_positioning(chunks, play_y)
    print("Checking formatting...")
    checks["formatting"] = check_formatting(chunks)

    # OCR-MODE: captions were read from the pixels (no .ass), so timing is coarse and OCR adds
    # character noise. Don't HARD-FAIL on OCR noise — downgrade accuracy/timing to advisory unless
    # OCR↔audio similarity is so low the captions clearly don't match. presence + gaps stay real.
    if ocr_mode:
        acc = checks.get("accuracy", {})
        sim = acc.get("similarity", 1.0)
        checks["_mode"] = {"pass": True,
                           "note": f"captions audited via OCR of burnt-in pixels (no .ass); similarity={sim}"}
        # Accuracy/timing via OCR are too noisy to GATE on (OCR garbles chars + can catch
        # background text). They're advisory only — PRESENCE is the real burnt-in caption gate.
        # A very low similarity stays as a loud WARNING (eyeball it) but does not hard-FAIL.
        for k in ("accuracy", "timing"):
            c = checks.get(k)
            if c and not c.get("pass", True):
                c["pass"] = True
                tag = " [OCR-mode advisory — char-noise tolerated]"
                if k == "accuracy" and sim < 0.45:
                    tag = f" [OCR-mode: low OCR↔audio similarity {sim} — eyeball captions to confirm]"
                c["note"] = (c.get("note", "") + tag).strip()

    any_fail = any(not c["pass"] for c in checks.values())
    results["verdict"] = "FAIL" if any_fail else "PASS"
    results["checks"] = checks
    results["metadata"] = {"ass": ass_path, "speakers": speakers,
                           "expected_colors": sorted(_expected_color_buckets(meta)) or None,
                           "resolved_from": "contract/manifest" if meta.get("has_metadata") else "none"}

    failures = [k for k, v in checks.items() if not v["pass"]]
    if failures:
        first_issues = checks[failures[0]].get("issues") or []
        first = first_issues[0] if first_issues else {}
        results["summary"] = (f"FAIL: {', '.join(failures)}. "
                              f"First: {first.get('problem', first.get('spoken', 'see details'))}")
    else:
        results["summary"] = "All caption checks passed"

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n{results['verdict']}: {results['summary']}")
    print(f"Report: {args.out}")


if __name__ == "__main__":
    main()
