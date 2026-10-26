#!/usr/bin/env python3
"""director_x — EXPRESSIVE spice caption director (Brand brand). Same schema/mechanics as
caption_director.py but tuned to visibly vary WEIGHT + SIZE + ITALIC per word (the dynamic
spice look Operator asked for), while keeping the hard rules (yellow quotes all-or-nothing,
size only on solo-word cues). Needs ANTHROPIC_API_KEY (pulled from the login shell)."""
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
import sys, json, argparse, subprocess, os, re, tempfile
from pathlib import Path

SYSTEM = """You are the caption "director" for dynamic SPICE short-form captions. You receive a \
word-level transcript (numbered words) and output a per-word STYLE STREAM as JSON. The look must be \
ALIVE and DYNAMIC — weight, size, and italic visibly change from word to word based on how the line \
is delivered. This is NOT flat subtitles. Be EXPRESSIVE (but never random/formulaic — style tracks meaning).

AXES:
- WEIGHT ("w") = vocal stress / importance. Tiers low->high: "mute"(throwaway glue), "base"(default), \
"soft", "strong", "emphasis", "payoff"(the punchline word of a thought). Push CONTRAST: ~55-70% of \
content words go ABOVE base; glue/function words (the,a,of,to,is,and,you,that,it,in,for,but,so...) stay \
base or mute. Adjacent words should usually DIFFER in weight so each line visibly varies. Vivid \
nouns/verbs/adjectives, negations (never/not), the turn/contrast, and the point get strong->emphasis; \
the single biggest word of a sentence gets payoff.
- ITALIC ("i":true) = lean/energy. Use it LIBERALLY (~20-35% of words) on: emphatic words, reflective \
or contrast words, adverbs/intensifiers (really, actually, literally, way, super), the setup of a \
flip ("don't... instead..."), and any wistful/soft delivery. Italic STACKS with weight. This is the \
movement Operator wants — most lines should have at least one italic word.
- SIZE ("s") = intensity: "emph","strong","peak". HARD RULE: size renders ONLY on a SINGLE-WORD caption. \
So ONLY set "s" on a word that will land alone on screen — a short word that ENDS its sentence (. ? !) or \
a one-word line. Reserve "peak" for THE biggest payoff (a clip's final/dramatic one-word beat). Use on \
~1 word in 8. NEVER size a word mid-phrase; emphasize those with weight+italic instead.
- COLOR via voice_spans (NOT per-word "c"): whenever the speaker QUOTES / ROLE-PLAYS / gives a \
"say this" script / reads an example message / voices someone else, mark the FULL contiguous span as \
[first_idx,last_idx]. Every word in it (incl. function words the,a,that,I,to,he...) renders yellow + \
italic + quoted — ALL-OR-NOTHING, never just the key word. Open a separate span per distinct quote; the \
speaker's own narration between quotes stays white. Numbers/money stay WHITE (use weight/size, not color).

OUTPUT: ONLY valid JSON, no prose/markdown. Schema:
{"words": {"<index>": {"w":"strong","s":"peak","i":true}, ...}, "voice_spans": [[first_idx,last_idx], ...]}
- "words": include EVERY word you style (omit only pure-default base words). Most lines should carry \
several weight changes + at least one italic. Do NOT put c/q here — use voice_spans for other-voice.
- "voice_spans": one [first,last] per quoted/role-played span; [] if pure first-person narration."""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript"); ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="claude-sonnet-4-5-20250929")
    ap.add_argument("--context", default="solo creator giving punchy advice")
    a = ap.parse_args()
    d = json.loads(Path(a.transcript).read_text())
    ws = d.get("words", d) if isinstance(d, dict) else d
    numbered = "\n".join(f'{i}: {w["word"]}' for i, w in enumerate(ws))
    user = (f"Clip context: {a.context}\nTotal words: {len(ws)}\n\nTranscript (index: word):\n"
            f"{numbered}\n\nReturn the dynamic JSON style stream now (vary weight+italic+size).")
    ak = (os.environ.get("ANTHROPIC_API_KEY")
          or subprocess.run(["zsh", "-ic", 'printf %s "$ANTHROPIC_API_KEY"'], capture_output=True, text=True).stdout.strip())
    if not ak:
        sys.exit("no ANTHROPIC_API_KEY")
    payload = {"model": a.model, "max_tokens": 8000, "system": SYSTEM,
               "messages": [{"role": "user", "content": user}]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
        json.dump(payload, tf); pf = tf.name
    r = subprocess.run(["curl", "-s", "https://api.anthropic.com/v1/messages",
                        "-H", f"x-api-key: {ak}", "-H", "anthropic-version: 2023-06-01",
                        "-H", "content-type: application/json", "-d", f"@{pf}"],
                       capture_output=True, text=True)
    os.unlink(pf)
    resp = json.loads(r.stdout)
    if "content" not in resp:
        sys.exit(f"API error: {json.dumps(resp)[:400]}")
    text = "".join(b.get("text", "") for b in resp["content"])
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        sys.exit(f"no JSON: {text[:300]}")
    stream = json.loads(m.group(0))
    stream.setdefault("words", {}); stream.setdefault("voice_spans", [])
    sw = stream["words"]
    for sp in (stream.get("voice_spans") or []):
        try:
            aa, bb = int(sp[0]), int(sp[1])
        except Exception:
            continue
        if aa > bb: aa, bb = bb, aa
        for idx in range(max(0, aa), min(len(ws) - 1, bb) + 1):
            v = sw.setdefault(str(idx), {}); v["c"] = "guest"; v["i"] = True; v["q"] = True
    stream["voice_spans"] = []
    json.dump(stream, open(a.out, "w"), indent=1)
    sized = sum(1 for v in sw.values() if v.get("s"))
    ital = sum(1 for v in sw.values() if v.get("i"))
    yel = sum(1 for v in sw.values() if v.get("c") == "guest")
    print(f"director_x: styled {len(sw)}/{len(ws)} ({ital} italic, {sized} sized, {yel} yellow)")


if __name__ == "__main__":
    main()
