#!/usr/bin/env python3
"""Caption-director — auto-emit the spice 5-axis per-word style stream via Claude.

Reads a word-level transcript, returns {words:{idx:{w,s,i,c,q}}, voice_spans:[]} applying the
reverse-engineered the reference editor logic + the locked hard rules. This is what makes the spice style the
hands-off DEFAULT: transcript in -> style stream out -> generate_spice renders it.

Usage: caption_director.py <transcript.json> --out <stream.json> [--model claude-sonnet-4-5-20250929] [--context "..."]
Needs ANTHROPIC_API_KEY (pulled from the login shell if not in env).
"""
# ── vibe-editing portable path bootstrap (auto-inserted) ──
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
    import pathlib as _pl
    parts = [x for x in str(p).strip("/").split("/") if x]
    if parts and parts[0] == "_shared":
        return _pl.Path(_os.path.join(VIBE_ROOT, "lib", *parts))
    return _pl.Path(_os.path.join(VIBE_SKILLS, *parts))
def _acqv(p):
    import pathlib as _pl
    return _pl.Path(_os.path.join(VIBE_VAULT, *[x for x in str(p).strip("/").split("/") if x]))
if VIBE_SHARED not in _sys.path:
    _sys.path.insert(0, VIBE_SHARED)
# ── end bootstrap ──
import sys, json, argparse, subprocess, os, re, tempfile
from pathlib import Path

SYSTEM = """You are the caption "director" for company.com / the creator short-form clips, \
replicating the team's lead editor the reference editor. You receive a word-level transcript (numbered words) \
and output a per-word STYLE STREAM as JSON. This style applies to ALL clip formats (Q&A, hotline, \
podcast, monologue) — the rules are universal.

The reference editor's style is GENEROUS with emphasis. ~55-60% of words get some styling. He paints in PHRASES, \
not isolated words — when a run of words carries meaning, the WHOLE run gets bumped together, \
including the function words inside it ("still have prices" = all three strong, not just "prices").

AXES:
- WEIGHT (key "w") = the reference editor's primary tool. Tiers: "base"(default ~40% of words), \
"strong"(~24% — broad emphasis runs), "payoff"(~10% — the key content clusters). \
CRITICAL PATTERN — the reference editor emphasizes in PHRASE RUNS, not single words. When a 2-5 word phrase \
carries the point, EVERY word in it gets the same tier — including "the", "a", "I", "in", "of", \
"is". He does NOT skip function words inside an emphasized phrase. Examples from his actual clips: \
"still have prices" = all three strong. "humans are still humans" = all four payoff. \
"really smart and work really hard" = all six strong. "my prices while" = all three payoff. \
Payoff marks content clusters (the revenue number, the punchline phrase, the key concept), \
not lone words. Strong paints broader runs of emphasis around them.
- SIZE (key "s") = ON (re-enabled 2026-06-11, confirmed vs the reference editor's reel). The reference editor ENLARGES the \
load-bearing words — size STACKS with weight (a key word is bigger AND bolder). Tiers: "emph" (the \
common ~1.25x bump — the punchy content word), "strong" (~1.5x — numbers/money, a strong payoff word), \
"peak" (~1.8x — RARE, the single biggest payoff word/line). ~25-30% of words get a size bump; most \
stay base. Bump the SAME words you weight-emphasize (the key noun/verb, numbers, the punchline), \
INCLUDING a key word inside a multi-word line (e.g. "sales and *marketing*" — marketing bumped). \
Do NOT bump function words ("and", "to", "the", "a", "of"). Emit "s" on emphasized words.
- NUMBERS & MONEY = BOLD (strong/emphasis weight) and they INHERIT THE SPEAKER'S COLOR — a guest's \
"$600K"/"$5M"/"40%" is YELLOW (it's inside the guest voice_span); Speaker's own "$3M"/"100%" stays white. \
Never force a number white if the guest said it. (Formatting like $K/M, #%, -20%, 2X, #1 is handled upstream by spice_normalize.)
- COLOR (key "c") = whose voice. "speaker"(white, default) or "guest"(yellow #FECB00 = a DIFFERENT \
speaker). In Q&A clips: the guest's entire turns are yellow via voice_spans. In monologues: yellow \
only for reported/role-played/quoted speech. LITMUS: if you could append "—they'd say" it's yellow; \
if it's Speaker making his own point, white. NEVER yellow Speaker's own narration, numbers, proper nouns, \
or emphasis words — use weight/size for those.
- ITALIC (key "i":true) = ~10% of words. Used selectively for extra emphasis, reflective \
asides, hypothetical phrases, single-word contrast. NOT blanket on all guest speech — most guest \
words are yellow but NOT italic. Only italic the words within guest speech that carry real emphasis.
- ⭐ VOICE_SPANS RULE (most important for multi-person clips). When there is a second speaker \
(Q&A guest, caller, workshop attendee), their ENTIRE contiguous turns are voice_spans: \
[first_word_index, last_word_index] (inclusive). EVERY word in the range goes yellow — including \
all function words. A span is ALL-OR-NOTHING. Where Speaker's narration resumes between guest turns, \
that stays white — open SEPARATE voice_spans for each guest turn. In a solo monologue, voice_spans \
covers only reported/role-played speech ("he said X" → X is a span). Catch ALL spans.
- ⭐⭐ BRIEF-INTERJECTION RULE (#1 color error — read carefully). In Q&A clips Speaker frequently \
drops 1-3 word acknowledgments MID-GUEST-TURN: "Cool" / "Love it" / "That's correct" / "Nice" / \
"Okay" / "Got it" / "Wow" / "Right". These are NOT the end of the guest's turn — the guest \
CONTINUES speaking immediately after. You MUST open a NEW voice_span for the guest's continuation. \
Typical pattern: Guest introduces themselves and their business ("I do X, we do $55M") → Speaker \
says "Cool" (1-2 white words) → Guest CONTINUES describing their situation ("We do 55 million in \
revenue..."). The "Cool" is white, but EVERYTHING the guest says after is a new yellow span. \
SCAN the full transcript for these interjection-gaps and ensure you open spans on BOTH sides. \
Clues: first-person pronouns after a gap ("we", "I", "our", "my") + business details (revenue, \
employees, market) = guest continuing, NOT Speaker.

PERFORMANCE SIGNALS (from a large short-form corpus — use to calibrate WHERE your peak emphasis lands):
Hook types by view lift: story 1.65x · bold_claim 1.26x · fear 1.21x · mistake 1.13x. \
Question openers: 0.48x (worst). Winning emotions: empathy 1.75x · excitement 1.65x · humor 1.60x \
· fear 1.49x · confidence 1.48x. Winning topics: Pricing 1.82x · Storytelling 1.75x · Relationships 1.63x.
What this means for emphasis: the HOOK PAYLOAD WORD is the single most important word in the clip — \
it's what makes someone stop scrolling. Identify the hook type from the opening line, then: \
(1) bold_claim/fear/story hook → the CLAIM WORD or the FEAR WORD in the first 1-3 lines gets "peak" SIZE, not just "strong". \
(2) If the clip evokes empathy or humor, the emotional peak line (the "aha" or the punchline) gets peak size even if it comes mid-clip. \
(3) Question openers are weak — if the first line IS a question, do NOT peak-size the question; instead find the ANSWER in the next 2-3 lines and peak-size the answer's payload word instead. \
(4) Numbers and money already get strong/emphasis — for HIGH-LIFT hook types (story/bold_claim/fear), size them UP one level (strong→peak, emph→strong) because the number IS the hook payload.

MEASURED REFINEMENTS (40 Q&A clips, 2026-06-14 — the reference editor corpus, apply these):
- ITALICS are COMMON — ~72% of his clips use them (NOT ~10%). Italicize quoted/role-played/reflective lines AND the load-bearing emphasis word inside guest turns liberally (i:true). When in doubt on an emphatic or reflective word, italic it.
- ACTIVE-WORD emphasis: keep the SIZE bump on the load-bearing word so the word that's being spoken is the one that pops.
- ONE clip may have a single PEAK word (the biggest hook/payoff word) at "s":"peak" — use peak at most once per clip; reserve it for the single most important word/line.
- Numbers/money: bump size + they inherit the speaker's color (guest=yellow), never colored white-on-Speaker.

OUTPUT: ONLY valid JSON, no prose, no markdown fences. Schema:
{"words": {"<index>": {"w":"strong","s":"peak","i":true}, ...}, "voice_spans": [[first_idx, last_idx], ...]}
- "words": weight + optional size "s" + optional italic. Omit a word entirely if it is base with no styling. \
Do NOT put "c" here for guest speech — use voice_spans. Emit "s" on the words you enlarge (key word, numbers, payoff). \
QUOTES ("q":true) ARE used (corpus 2026-06-10 corrected the old "no quotes" rule): wrap reported/role-played/ \
imagined speech in quotes WITH italic (curly quotes render automatically), inheriting the speaker's color — \
e.g. spoken 'people are like this is recent' → those words get q+i. A NAMED TERM ("key man risk") gets q WITHOUT italic.
- "voice_spans": one [first, last] (inclusive) per guest turn or reported-speech span; [] for pure solo.

EXAMPLE (numbered "...0:I 1:sell 2:Christmas 3:light 4:installation. 5:Love 6:it. 7:Last 8:year \
9:we 10:did 11:450k. 12:What 13:stops 14:you 15:from 16:just 17:saying 18:a 19:higher 20:price?"):
{"words": {"3":{"w":"payoff","i":true}, "4":{"w":"payoff","i":true}, "10":{"w":"payoff"}, \
"11":{"w":"payoff"}, "12":{"w":"strong"}, "13":{"w":"strong"}, "14":{"w":"strong"}, \
"18":{"w":"strong"}, "19":{"w":"strong"}, "20":{"w":"strong"}}, "voice_spans": [[0, 4], [7, 11]]}
(Guest turn 0-4: "I sell Christmas light installation" = all yellow, "light installation" = payoff+italic \
within the span. Speaker 5-6: "Love it" = white, default. Guest turn 7-11: "Last year we did 450k" = all \
yellow, "did 450k" = payoff cluster. Speaker 12-20: "What stops you from just saying a higher price?" = \
white, "What stops you" strong run, "a higher price?" strong run — function words "a" included.)
"""


_STOP = {"the","a","an","and","or","but","is","are","was","were","be","i","you","we","he",
         "she","it","they","this","that","of","to","in","on","for","with","as","at","by",
         "from","into","that","my","your","our","their","what","when","why","how","who",
         "so","like","just","do","does","did","have","has","had","get","got","up","down",
         "out","over","not","no","yes","yeah","ok","okay","if","then","than","also"}


def _tokens(text: str):
    import re
    return [t for t in re.findall(r"[a-z']+", text.lower()) if t not in _STOP and len(t) > 2]


def _similarity(needle_tokens, hay_tokens):
    """Jaccard similarity over content tokens — cheap, no model needed."""
    a, b = set(needle_tokens), set(hay_tokens)
    if not a or not b: return 0.0
    return len(a & b) / len(a | b)


def deterministic_stream(ws):
    """Credit-free emphasis when no LLM is available — applies the LOCKED the reference editor rule
    'emphasis = bigger + bolder on the load-bearing word' deterministically:
      - any number/money/% token -> emphasis weight + 'strong' SIZE (bigger & bold), and
      - the single longest content word per ~3-word window -> strong weight + 'emph' SIZE.
    Both WEIGHT and SIZE are emitted (size axis re-enabled 2026-06-11) so captions are lively
    even without the LLM director. Color is left to the caller (EDL speaker truth)."""
    import re as _re
    def _isnum(t):
        t = t.strip(".,!?\"'")
        return bool(_re.search(r"\d", t)) or t.startswith("$") or t.endswith("%")
    words, n = {}, len(ws)
    for i, w in enumerate(ws):                       # numbers/money: bold + bigger
        if _isnum(str(w.get("word", ""))):
            words[str(i)] = {"w": "emphasis", "s": "strong"}
    for s in range(0, n, 3):                          # one key word per ~3-word cue: bold + bumped
        win = [(j, str(ws[j].get("word", "")).strip(".,!?\"'")) for j in range(s, min(s + 3, n))]
        cands = [(j, t) for j, t in win if str(j) not in words
                 and t.lower() not in _STOP and len(t) >= 5 and t.isalpha()]
        if cands:
            j, _ = max(cands, key=lambda x: len(x[1]))
            words[str(j)] = {"w": "strong", "s": "emph"}
    return {"words": words, "voice_spans": []}


def load_exemplars(exemplars_dir: Path, clip_type_hint: str, n: int = 3,
                   query_transcript: str = ""):
    """Pick up to N the reference editor exemplars MOST SIMILAR to the current job.

    The corpus lives in caption-app/training/exemplars/ — each entry pairs a
    transcript with the per-word style stream we extracted from the reference editor's burned
    captions. Putting these in the prompt as few-shot examples teaches the
    director the reference editor's actual decision pattern.

    Ranking:
      1) hard filter by clip_type (workshop / hotline / monologue)
      2) rank by Jaccard similarity of CONTENT tokens between the new transcript
         and each exemplar — so a Q&A about pricing retrieves other pricing Q&As,
         a clip with rapid back-and-forth retrieves other alternating-style clips
      3) ties broken by recency (latest the reference editor style first)
    """
    if not exemplars_dir or not exemplars_dir.exists():
        return []
    typ = "workshop" if ("q&a" in clip_type_hint.lower() or "workshop" in clip_type_hint.lower()) \
        else "hotline" if "hotline" in clip_type_hint.lower() \
        else None
    q_tokens = _tokens(query_transcript) if query_transcript else []
    scored = []
    for p in sorted(exemplars_dir.glob("*.exemplar.json")):
        try:
            ex = json.loads(p.read_text())
        except Exception:
            continue
        if typ and ex.get("meta", {}).get("clip_type") != typ:
            continue
        if q_tokens:
            ex_text = " ".join(w["word"] for w in ex.get("words", []))
            sim = _similarity(q_tokens, _tokens(ex_text))
        else:
            sim = 0.0
        scored.append((sim, ex.get("meta", {}).get("extracted_at", ""), ex))
    # Highest similarity first, then most recent
    scored.sort(key=lambda t: (-t[0], t[1]), reverse=False)
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [t[2] for t in scored[:n]]


def format_exemplar(ex):
    """Format one exemplar for the prompt: numbered transcript + the reference editor's labels."""
    meta = ex.get("meta", {})
    words = ex.get("words", [])
    stream = ex.get("spice_stream", {})
    numbered = "\n".join(f'{w["i"]}: {w["word"]}' for w in words)
    label_json = json.dumps({"words": stream.get("words", {}),
                             "voice_spans": stream.get("voice_spans", [])})
    return (f"--- EXAMPLE: {meta.get('clip_type','?')} clip "
            f"\"{meta.get('title','?')}\" ({meta.get('duration_s','?')}s) ---\n"
            f"Transcript:\n{numbered}\n\n"
            f"The reference editor's labels (what we extracted from his burned-in captions):\n"
            f"{label_json}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript")
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="claude-sonnet-4-5-20250929")
    ap.add_argument("--context", default="solo the creator motivational monologue")
    _DEFAULT_EX = _acqv("caption-app/training/exemplars")
    ap.add_argument("--exemplars-dir", type=Path,
                    default=(_DEFAULT_EX if _DEFAULT_EX.exists() else None),
                    help="Directory of <name>.exemplar.json files (The reference editor's GOLD bank, restyled "
                         "2026-06-10; size axis re-enabled 2026-06-11 — see deterministic_stream/prompt). "
                         "Defaults to the bank so the 3 most-similar are ALWAYS injected as few-shot — "
                         "the director replicates the reference editor's actual decisions, not just the prompt. "
                         "Pass a different dir or '' to override.")
    ap.add_argument("--exemplars-n", type=int, default=3,
                    help="How many exemplars to put in the prompt (default 3).")
    a = ap.parse_args()

    d = json.loads(Path(a.transcript).read_text())
    ws = d.get("words", d) if isinstance(d, dict) else d
    numbered = "\n".join(f'{i}: {w["word"]}' for i, w in enumerate(ws))

    # Few-shot: paste up to N similar the reference editor exemplars BEFORE the new transcript.
    # The system prompt's rules tell Claude what to do in general; the exemplars
    # show what the reference editor actually did on real clips of the same type.
    exemplar_block = ""
    if a.exemplars_dir:
        # Pass the new clip's transcript text so retrieval can rank by content similarity
        query_text = " ".join(w["word"] for w in ws)
        exs = load_exemplars(a.exemplars_dir, a.context,
                             n=a.exemplars_n, query_transcript=query_text)
        if exs:
            exemplar_block = (
                "BEFORE labeling the new clip below, study these EXAMPLES — each is one "
                "of the reference editor's past clips with the labels we extracted from his rendered "
                "captions. Mimic his patterns (chunking rhythm, when yellow vs white, "
                "which words get weight bumps, how voice_spans are scoped). Then apply "
                "the same judgment to the new clip.\n\n"
                + "\n\n".join(format_exemplar(ex) for ex in exs)
                + "\n=== END EXAMPLES — apply the same style to the NEW clip below ===\n\n"
            )
            print(f"director: injected {len(exs)} exemplar(s) from {a.exemplars_dir}")

    user = (exemplar_block
            + f"Clip context: {a.context}\nTotal words: {len(ws)}\n\n"
            + f"Transcript (index: word):\n{numbered}\n\nReturn the JSON style stream now.")

    # LLM path (Anthropic) for full semantic styling; on ANY failure (no key, credits, bad
    # response) fall back to the DETERMINISTIC emphasis stream so /edit one-shots regardless.
    stream = None
    ak = os.environ.get("ANTHROPIC_API_KEY")
    if not ak and os.name != "nt":  # zsh login-shell lookup is macOS/Linux only
        try:
            ak = subprocess.run(["zsh", "-ic", 'printf %s "$ANTHROPIC_API_KEY"'],
                                capture_output=True, text=True).stdout.strip()
        except FileNotFoundError:
            ak = ""
    if ak:
        payload = {"model": a.model, "max_tokens": 8000, "system": SYSTEM,
                   "messages": [{"role": "user", "content": user}]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(payload, tf); pf = tf.name
        r = subprocess.run(["curl", "-s", "https://api.anthropic.com/v1/messages",
                            "-H", f"x-api-key: {ak}", "-H", "anthropic-version: 2023-06-01",
                            "-H", "content-type: application/json", "-d", f"@{pf}"],
                           capture_output=True, text=True)
        os.unlink(pf)
        try:
            resp = json.loads(r.stdout)
            text = "".join(b.get("text", "") for b in resp.get("content", []))
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                stream = json.loads(m.group(0))
            else:
                print(f"director: no JSON from LLM ({json.dumps(resp)[:160]}) -> deterministic", file=sys.stderr)
        except Exception as e:
            print(f"director: LLM error ({str(e)[:120]}) -> deterministic", file=sys.stderr)
    else:
        print("director: no ANTHROPIC_API_KEY -> trying claude CLI", file=sys.stderr)
    # CLI fallback: if the API path produced nothing (no key, or out of credits / bad response),
    # route the SAME system+user prompt through the `claude` CLI (the user's subscription). This
    # mirrors thread_mine.py and keeps real the reference editor-style direction working when the API is down —
    # WITHOUT it the director silently degrades to flat deterministic emphasis (the "wrong captions").
    if stream is None:
        try:
            proc = subprocess.run(
                ["claude", "-p", f"{SYSTEM}\n\n---INPUT---\n{user}"],
                capture_output=True, text=True, timeout=300)
            if proc.returncode == 0 and proc.stdout:
                m = re.search(r"\{.*\}", proc.stdout, re.S)
                if m:
                    stream = json.loads(m.group(0))
                    print("director: styled via claude CLI", file=sys.stderr)
            if stream is None:
                print(f"director: claude CLI returned no JSON -> deterministic "
                      f"({(proc.stderr or proc.stdout or '')[:160]})", file=sys.stderr)
        except Exception as e:
            print(f"director: claude CLI failed ({str(e)[:120]}) -> deterministic", file=sys.stderr)
    if stream is None:
        stream = deterministic_stream(ws)
    stream.setdefault("words", {}); stream.setdefault("voice_spans", [])
    # Expand reported-speech voice_spans (word-index ranges) -> per-word yellow+italic+quotes, so a
    # quoted phrase is ALL-OR-NOTHING: every word in the span (incl. function words) is guest+i+q,
    # never left white mid-quote. Emphasis (w/s) the model set per-word is preserved.
    sw = stream["words"]
    for sp in (stream.get("voice_spans") or []):
        try:
            aa, bb = int(sp[0]), int(sp[1])
        except (TypeError, ValueError, IndexError):
            continue
        if aa > bb:
            aa, bb = bb, aa
        aa = max(0, aa); bb = min(len(ws) - 1, bb)
        for idx in range(aa, bb + 1):
            d = sw.setdefault(str(idx), {}); d["c"] = "guest"
    # Emit EMPTY voice_spans: the per-word c/i/q set above is the SINGLE source of truth. Do NOT hand
    # generate_spice a time-range — its voice_of() fallback colors any word WITHOUT an explicit c whose
    # start falls in [t0,t1), which bleeds onto the next word when the range end ~ that word's start
    # (it colored "you're" @18.70s yellow when the span ended 18.78s — Mediocrity, 2026-06-04).
    stream["voice_spans"] = []

    # --- Interjection-gap auto-fix (2026-06-08) ---
    # In Q&A clips, the model sometimes creates a guest span for the intro, then Speaker
    # drops a 1-3 word acknowledgment ("Cool", "Love it"), and the model forgets to
    # open a NEW span for the guest's continuation. This safety net detects that
    # pattern and auto-marks the continuation as guest.
    _is_qa = ("q&a" in a.context.lower() or "two-person" in a.context.lower()
              or "workshop" in a.context.lower())
    if _is_qa:
        _INTERJ = {"cool","nice","okay","ok","wow","right","yeah","yes","got","it",
                    "love","that's","correct","good","great","sure","absolutely",
                    "interesting","amazing","perfect","fair","exactly","totally",
                    "sweet","awesome","solid","hundred","percent","hundred percent"}
        _1P = {"we","i","my","our","us","me","i'm","we're","we've","i've"}
        guest_idxs = sorted(int(k) for k,v in sw.items() if v.get("c")=="guest")
        if guest_idxs:
            # Find contiguous guest runs
            _runs, _rs = [], guest_idxs[0]
            for _j in range(1, len(guest_idxs)):
                if guest_idxs[_j] != guest_idxs[_j-1] + 1:
                    _runs.append((_rs, guest_idxs[_j-1]))
                    _rs = guest_idxs[_j]
            _runs.append((_rs, guest_idxs[-1]))
            # Check EVERY run boundary (not just last) for interjection gaps
            # Also check after the last run for orphaned guest continuation
            check_points = []
            for ri in range(len(_runs) - 1):
                gap_start = _runs[ri][1] + 1
                gap_end = _runs[ri+1][0] - 1
                check_points.append((_runs[ri][1], gap_start, gap_end, _runs[ri+1][0]))
            # After last run: check for orphaned tail
            last_end = _runs[-1][1]
            tail_len = len(ws) - 1 - last_end
            if tail_len > 8:
                check_points.append((last_end, last_end + 1, None, None))
            for (_prev_end, _gap_s, _gap_e, _next_run_s) in check_points:
                # Measure interjection gap length
                _interj_len = 0
                for _gi in range(_gap_s, min(_gap_s + 6, len(ws))):
                    _gw = ws[_gi]["word"].lower().strip(".,!?;:'\"")
                    if _gw in _INTERJ:
                        _interj_len += 1
                    else:
                        break
                if _interj_len < 1 or _interj_len > 5:
                    continue
                _cont_start = _gap_s + _interj_len
                if _next_run_s is not None:
                    continue  # gap between two existing runs — model already handled both sides
                # This is a tail after the last run. Check first-person in next 8 words.
                _probe = [ws[i]["word"].lower().strip(".,!?;:'\"")
                          for i in range(_cont_start, min(_cont_start + 8, len(ws)))]
                if not any(p in _1P for p in _probe):
                    continue
                # Find where guest continuation ends: next "?" = Speaker asking a question
                _cont_end = len(ws) - 1
                for _si in range(_cont_start, len(ws)):
                    if ws[_si]["word"].strip().endswith("?"):
                        # Scan back to find the question's start (question words before the ?)
                        _q_start = _si
                        _QW = {"what","why","how","when","where","who","which","do","does",
                               "did","are","is","can","could","would","should","have","you"}
                        for _qi in range(_si - 1, max(_cont_start - 1, _si - 10), -1):
                            if ws[_qi]["word"].lower().strip(".,!?;:'\"") in _QW:
                                _q_start = _qi
                            else:
                                break
                        _cont_end = _q_start - 1
                        break
                if _cont_end >= _cont_start:
                    _gap_words = [ws[i]["word"] for i in range(_gap_s, _gap_s + _interj_len)]
                    print(f"  ⚠ interjection-gap auto-fix: guest ended at word {_prev_end}, "
                          f"gap={_gap_words}, auto-marking [{_cont_start}-{_cont_end}] as guest")
                    for _idx in range(_cont_start, _cont_end + 1):
                        sw.setdefault(str(_idx), {})["c"] = "guest"

    json.dump(stream, open(a.out, "w"), indent=1)
    sw = stream["words"]
    sized = sum(1 for v in sw.values() if v.get("s"))
    print(f"director [{a.model}]: styled {len(sw)}/{len(ws)} words ({sized} sized, "
          f"{sum(1 for v in sw.values() if v.get('c')=='guest')} yellow) -> {a.out}")


if __name__ == "__main__":
    main()
