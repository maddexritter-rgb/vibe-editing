#!/usr/bin/env python3
"""SCRIPT-DRIVEN cutter  ==  text-based editing (the Descript model), done with forced alignment.

THE METHOD (this is the whole idea):
  1. take the SCRIPT = the exact clean words you want the clip to say
  2. forced-align it to the audio to find where every word physically is
  3. keep the word-spans, CUT everything in between.

"Everything in between" means BOTH silence AND junk (fillers, false starts, restarts).
Junk is never in the script, so it lives in the gaps between kept words -> it gets cut.
You never surgically remove errors; you only keep the words you chose, so errors can't leak.

WHY align the FULL transcript, not just the clean script?
  Forced alignment can't *skip* unscripted audio -- it absorbs a stumble into a neighbouring
  word's boundary, inflating that word's span so the stumble rides along in the cut. So we align
  the faithful transcript (every word incl. fillers/restarts), then KEEP only the curated subset
  and cut the rest. The kept words land on their true positions; the junk stays in the cut gaps.

INPUT
  --source       the source video/audio (mp4/mov/wav)
  --transcript   word-level transcript JSON: {"segments":[{"words":[{word,start,end}...]}]} OR
                 {"words":[...]} OR a flat list. (Groq/WhisperX style.)
  --spec         "structure" JSON: {"segments":[{"in":sec,"out":sec, "n":1?, "cat":"LABEL"?}, ...]}
                 in/out are the editor's ROUGH marks per chunk/lesson; n+cat are optional passthrough
                 (e.g. listicle numbered tabs). The cut is defined by the SCRIPT, not these marks --
                 in/out only choose which words belong to each chunk.
  --out          output dir; writes <out>/cut_spec.json (the precise, frame-honest cut to render)

OUTPUT
  <out>/cut_spec.json -> {"title", "segments":[{"in","out","n"?,"cat"?}, ...]}  in SOURCE seconds.
  Feed that to your renderer (e.g. listicle-short/build_short.py, or precision_cut/ffmpeg).

TOOLCHAIN (see SKILL.md + setup_toolchain.sh): Montreal Forced Aligner (MFA) via micromamba, plus a
python venv with numpy + num2words. Run THIS script with that venv's python.
Env: MFA_MAMBA (micromamba bin), MFA_ENV (mfa conda env), MFA_ACOUSTIC/MFA_DICT (default english_us_arpa).

KEY LESSONS BAKED IN (each was a real bug we hit):
  * NEVER sort the transcript by timestamp -- glitchy ASR timestamps reorder words. File order = spoken order.
  * Numbers: keep "250"/"2024" (a digit IS a real word); spell them for the MFA dict (num2words, year-aware).
  * MFA edge-pull: if the wav has unscripted lead audio, MFA glues the first word to t=0 and drifts ~0.3s.
    Fix: start the wav right at the first context word (minimal pre-roll); select from the editor mark forward.
  * Stretched function word (a 1.18s "it") = the transcript hiding a stumble. Tell stumble from pause by
    LISTENING: if the word's interior stays as loud as its onset it's a stumble (drop it); if it goes quiet
    it's just a pause (keep the word). hides_speech() does this.
  * true_end(): MFA labels soft endings (-ing/-s/-ve) early. Extend each cut to the word's TRUE acoustic
    end (where energy falls into the trailing silence) so tails aren't clipped and payoffs land.
"""
import tempfile
import sys, json, glob, os, subprocess, re, shutil, argparse
import numpy as _np
from num2words import num2words

ap = argparse.ArgumentParser()
ap.add_argument("--source", required=True)
ap.add_argument("--transcript", required=True)
ap.add_argument("--spec", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--gap", type=float, default=float(os.environ.get("GAP_CUT", "0.20")),
                help="cut any inter-kept-word gap >= this (seconds). bigger = looser/longer pauses kept")
ap.add_argument("--title", default=None)
A = ap.parse_args()

GAP_CUT = A.gap
PAD     = 0.04
src     = A.source
NAME    = A.title or os.path.splitext(os.path.basename(A.source))[0]
os.makedirs(A.out, exist_ok=True)
MAMBA    = os.environ.get("MFA_MAMBA") or os.path.join(tempfile.gettempdir(), "bin", "micromamba")
MFA_ENV  = os.environ.get("MFA_ENV") or os.path.join(tempfile.gettempdir(), "mfa_env")
ACOUSTIC = os.environ.get("MFA_ACOUSTIC", "english_us_arpa")
DICT     = os.environ.get("MFA_DICT", "english_us_arpa")

# ---------- load the word-level transcript (NEVER sort it; file order is spoken order) ----------
d = json.load(open(A.transcript))
if isinstance(d, dict) and "segments" in d: stt = d["segments"]
elif isinstance(d, dict) and "words" in d:  stt = [d]
else:                                        stt = d
W = []                                                # (start, end, word)
for s in stt:
    ws = s["words"] if isinstance(s, dict) and "words" in s else [s] if isinstance(s, dict) else []
    for x in ws:
        if x.get("start") is not None:
            W.append((float(x["start"]), float(x.get("end") or x["start"]), (x.get("word") or x.get("text") or "").strip()))

FILL = {'uh','um','uhh','umm','mm','hmm','er','ah','mhm','uhm'}
CONN = {'and','but','so','or','well','like','now','okay','right'}
def cw(w): return re.sub(r"[^a-z']", "", w.lower())
def real(w):                                          # real word = has a letter OR digit and isn't a filler
    s = re.sub(r"[^a-z0-9']", "", w.lower())
    return bool(s) and s not in FILL
def _frames_db(s, e):                                 # per-frame (20ms win / 10ms hop) dB of a source span
    if e <= s: return _np.array([-120.0])
    raw = subprocess.run(["ffmpeg","-v","error","-ss",f"{s:.3f}","-t",f"{e-s:.3f}","-i",src,
                          "-ac","1","-ar","16000","-f","f32le","-"], capture_output=True).stdout
    x = _np.frombuffer(raw, _np.float32); wl, hop = 320, 160
    if len(x) < wl: return _np.array([-120.0])
    return _np.array([20*_np.log10(_np.sqrt(_np.mean(x[i:i+wl]**2))+1e-9) for i in range(0, len(x)-wl, hop)])
def hides_speech(s, e):                               # stretched word interior: continuous speech (stumble) vs a pause?
    hd = float(_np.median(_frames_db(s, min(e, s+0.18))))
    tail = _frames_db(s+0.25, e-0.03)
    return float(_np.mean(tail > hd - 12)) > 0.55     # >55% of interior as loud as the word = STUMBLE -> drop
def true_end(go, limit):                              # extend the cut to the word's RELEASE-INTO-SILENCE (clean taper)
    b = min(limit, go + 0.50)                                 # aligners label soft endings (-ing/-s/-ve) early
    if b <= go + 0.04: return float(min(go + 0.06, limit))
    fr = _frames_db(go - 0.10, b)                             # frames from 0.10s before the labelled end (10ms hop)
    body = _frames_db(go - 0.12, go - 0.02)                   # the word body's loudness, as reference
    ref = float(_np.median(body)) if len(body) > 1 else float(_np.median(fr))
    sil = ref - 16.0                                          # 16 dB under the word = released into silence
    run = 0; end_t = b
    for i in range(min(10, len(fr)-1), len(fr)):              # walk forward from the labelled end (index ~10)
        if fr[i] < sil:
            run += 1
            if run >= 4: end_t = (go - 0.10) + (i - 3) * 0.01; break   # first sustained 40ms of silence = release done
        else: run = 0
    return float(min(max(end_t + 0.02, go + 0.02), limit))
def spell(word):                                      # one raw word -> spelled tokens for the MFA dictionary
    def _sp(m):
        nn = m.group(0); v = int(nn)
        return num2words(v, to="year") if (len(nn) == 4 and 1900 <= v <= 2099) else num2words(v)
    t = re.sub(r"%", " percent ", word)
    t = re.sub(r"\d+", _sp, t)
    t = re.sub(r"[^a-zA-Z' ]", " ", t)
    return [tok.lower() for tok in t.split() if tok]

STRETCH_FN = {'it','is','a','the','to','of','and','that','this','because','i','was','its','so','which'}
LEAD_DROP  = CONN | {'because','which','cause'}

# ---------- build the curated SCRIPT for each chunk ----------
spec = json.load(open(A.spec))
lessons = []
for seg in spec["segments"]:
    i0, o0 = float(seg["in"]), float(seg["out"]); n, cat = seg.get("n"), seg.get("cat")
    idxs = [i for i, w in enumerate(W) if w[0] >= i0 - 0.20 and w[0] < o0 + 0.25]   # editor mark -> forward
    if not idxs: continue
    a, b = idxs[0], idxs[-1]
    cur = [(i, W[i][0], W[i][1], W[i][2]) for i in range(a, b+1) if real(W[i][2])]
    if not cur: continue
    while len(cur) >= 2:                                                            # LEADING junk cleanup
        c0 = cw(cur[0][3])
        if c0 in LEAD_DROP: cur = cur[1:]; continue
        if c0 in STRETCH_FN and (cur[0][2]-cur[0][1]) > 0.6 and hides_speech(cur[0][1], cur[0][2]):
            cur = cur[1:]; continue                                                # stretched fn hiding a STUMBLE
        if c0 and c0 == cw(cur[1][3]) and cur[1][1]-cur[0][2] < 0.5: cur = cur[1:]; continue  # "is is" stutter
        break
    def ends_sent(w): return w.rstrip().endswith(('.','!','?'))                     # trailing ORPHAN trim
    le = max((k for k,(i,s,e,w) in enumerate(cur) if ends_sent(w)), default=-1)
    if 0 <= le < len(cur)-1 and (len(cur)-1-le) <= 4: cur = cur[:le+1]
    while cur and cw(cur[-1][3]) in CONN: cur = cur[:-1]
    if not cur: continue
    lead  = W[cur[0][0]-1]  if cur[0][0]-1 >= 0 else None
    trail = W[cur[-1][0]+1] if cur[-1][0]+1 < len(W) else None
    if lead  and cur[0][1]  - lead[1]  > 0.7: lead  = None
    if trail and trail[0] - cur[-1][2] > 0.7: trail = None
    curw = [(s,e,w) for i,s,e,w in cur]
    lessons.append((n, cat, curw, lead, trail, i0, o0))

# ---------- forced-align each chunk's script (+1 context word per side, for outer clamp) ----------
corpus = os.path.join(A.out, "_corpus"); shutil.rmtree(corpus, ignore_errors=True); os.makedirs(corpus)
meta = []
print("=== curated scripts (faithful order) ===", flush=True)
for idx, (n, cat, cur, lead, trail, i0, o0) in enumerate(lessons):
    lead_tok  = spell(lead[2])  if lead  else []
    trail_tok = spell(trail[2]) if trail else []
    script_tok = []
    for st, en, wd in cur: script_tok += spell(wd)
    align_tok = lead_tok + script_tok + trail_tok
    if not align_tok: continue
    print(f"  [{(cat or 'HOOK'):11s}] {' '.join(w[2] for w in cur)}", flush=True)
    ss = (lead[0] if lead else cur[0][0]) - 0.05      # minimal pre-roll so MFA doesn't absorb unscripted lead audio
    te = (trail[1] if trail else cur[-1][1]) + 0.20
    subprocess.run(["ffmpeg","-y","-v","error","-ss",f"{ss}","-t",f"{te-ss}","-i",src,
                    "-ac","1","-ar","16000",f"{corpus}/{idx:02d}.wav"])
    open(f"{corpus}/{idx:02d}.txt","w").write(" ".join(align_tok))
    meta.append((idx, n, cat, ss, len(lead_tok), len(script_tok), len(trail_tok)))

od = os.path.join(A.out, "_aligned"); shutil.rmtree(od, ignore_errors=True)
print(f"aligning {len(meta)} chunks (script-driven, GAP_CUT={GAP_CUT}s) with MFA...", flush=True)
subprocess.run([MAMBA,"run","-p",MFA_ENV,"mfa","align","--clean","--single_speaker","--quiet",
                corpus, DICT, ACOUSTIC, od], check=True)

# ---------- assemble: keep word-spans, cut everything in between ----------
def parse_words(tg, ss):
    body = open(tg).read()
    m = re.search(r'name = "words"(.*?)item \[2\]', body, re.S)                     # WORDS tier ONLY (not phonemes)
    ivs = re.findall(r'xmin = ([\d.]+)\s+xmax = ([\d.]+)\s+text = "([^"]*)"', m.group(1) if m else "")
    return [(ss + float(a), ss + float(b), t.lower()) for a, b, t in ivs if t.strip()]

new = {"title": NAME, "segments": []}
print("\n=== assembly (each chunk) ===", flush=True)
for idx, n, cat, ss, nl, nscr, nt in meta:
    tg = f"{od}/{idx:02d}.TextGrid"; label = cat or "HOOK"
    def fallback(reason):
        cur = lessons[idx][2]
        new["segments"].append({"in": round(cur[0][0]-PAD,3), "out": round(cur[-1][1]+PAD,3),
                                **({"n":n} if n is not None else {}), **({"cat":cat} if cat else {})})
        print(f"  [{label:11s}] !! {reason}, fallback span", flush=True)
    if not os.path.exists(tg): fallback("no TextGrid"); continue
    aw = parse_words(tg, ss)
    if len(aw) != nl + nscr + nt: fallback(f"token mismatch ({len(aw)} vs {nl+nscr+nt})"); continue
    lead_a  = aw[nl-1] if nl else None
    trail_a = aw[nl+nscr] if nt else None
    script_a = aw[nl:nl+nscr]
    spans = [[script_a[0]]]; cuts = []                                              # gap-cut
    for w in script_a[1:]:
        gap = w[0] - spans[-1][-1][1]
        if gap >= GAP_CUT: cuts.append((spans[-1][-1][2], w[2], round(gap,2))); spans.append([w])
        else: spans[-1].append(w)
    for k, sp in enumerate(spans):
        gi, go = sp[0][0], sp[-1][1]
        prev_end   = spans[k-1][-1][1] if k > 0 else (lead_a[1]  if lead_a  else None)
        next_start = spans[k+1][0][0]  if k < len(spans)-1 else (trail_a[0] if trail_a else None)
        lo = gi - PAD
        if prev_end is not None: lo = max(lo, prev_end + 0.005)
        lo = min(lo, gi)                                                            # never clip the onset
        lim = (next_start - 0.005) if next_start is not None else go + 0.40
        hi = true_end(go, lim)                                                      # END at true acoustic release
        sg = {"in": round(lo,3), "out": round(hi,3)}
        if k == 0:
            if n is not None: sg["n"] = n
            if cat: sg["cat"] = cat
        new["segments"].append(sg)
    cutinfo = "  ".join(f"[cut {g}s after '{x}']" for x,_,g in cuts) or "(no internal cuts)"
    print(f"  [{label:11s}] {len(spans)} span(s)  {cutinfo}", flush=True)
    print(f"               says: {' '.join(w[2] for w in script_a)}", flush=True)

outspec = os.path.join(A.out, "cut_spec.json")
json.dump(new, open(outspec, "w"), indent=2)
print(f"\n{NAME}: wrote {outspec} ({len(new['segments'])} segments). Feed it to your renderer.", flush=True)
