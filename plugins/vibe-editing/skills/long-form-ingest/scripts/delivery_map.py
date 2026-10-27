#!/usr/bin/env python3
"""Gemini delivery map: how each transcript word actually sounds, tagged by word index.

Run once per source after AssemblyAI. Each chunk of played audio is sent with its
numbered transcript, and Gemini tags notable delivery on word indices (its own
timestamps are only ~1 s accurate, so time is never used to place a tag). The map is
evidence for the editor-model; it selects nothing and approves nothing. Chunks that
fail stay UNAVAILABLE and the run still succeeds.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import difflib
import hashlib
import re
import subprocess
import tempfile
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'lib/_shared'))
import gemini_audio  # noqa: E402

SCHEMA = 'ingest.delivery-map/v1'
TYPES = {
    'misheard': 'transcript words differ from what is audible (put the audible words in heard)',
    'phantom_word': 'a transcript word that is not actually audible',
    'missing_word': 'audible speech missing from the transcript (put the audible words in heard)',
    'filler_audible': 'an audible um/uh/like/you-know style filler',
    'stumble_restart': 'false start, restart, repeated or abandoned words',
    'slurred_or_swallowed': 'word present but mumbled, clipped or swallowed in the source',
    'emphasis': 'deliberately stressed or high-energy delivery',
    'energy_drop': 'trailing off, low energy, mumbled ending',
    'laugh': 'laughter by the speaker or others',
    'breath_noise': 'loud breath, lip smack, cough or mouth noise next to the word',
    'crosstalk_or_noise': 'overlapping speaker or background noise over the word',
    'meaningful_pause': 'a pause that carries timing or emphasis, not dead air',
}
CONFIDENCE = {'high', 'medium', 'low'}
# Tags where Gemini disputes the transcript text; a third transcriber breaks the tie.
DISPUTES = {'misheard', 'phantom_word', 'missing_word'}
FILLERS = {'um', 'uh', 'ah', 'erm', 'hmm', 'mm', 'like', 'yeah'}
SPELLINGS = {"'cause": 'because', 'cause': 'because', 'cuz': 'because', 'cos': 'because',
             'outta': 'out of', 'gonna': 'going to', 'wanna': 'want to', 'gotta': 'got to'}


ONES = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split()
TENS = 'twenty thirty forty fifty sixty seventy eighty ninety'.split()


def number_words(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10 - 2] + ('' if n % 10 == 0 else ' ' + ONES[n % 10])
    if n < 1000:
        return ONES[n // 100] + ' hundred' + ('' if n % 100 == 0 else ' ' + number_words(n % 100))
    return str(n)


def norm(text):
    out = []
    text = re.sub(r'\b(\d{1,3})\b', lambda m: number_words(int(m.group(1))), str(text).lower())
    for tok in re.sub(r"[^a-z0-9' ]+", ' ', text).split():
        out.extend(SPELLINGS.get(tok, tok.strip("'")).split())
    return ' '.join(t for t in out if t)


def local_whisper(audio, start, end):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'lib/_shared'))
    import asr
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / 'w.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{start:.3f}', '-t', f'{end - start:.3f}', '-i', str(audio),
                        '-ac', '1', '-ar', '16000', str(wav)], check=True)
        result = asr._mlx(str(wav))
    return (result or {}).get('text')


def tiebreak(words, audio, tag, transcriber=local_whisper, margin=0.25):
    """Vote AssemblyAI vs Gemini on a disputed span using an independent local transcriber."""
    a, b = tag['word_first'], tag['word_last']
    lo, hi = max(0, a - 3), min(len(words) - 1, b + 3)
    before = [w['word'] for w in words[lo:a]]
    span = [w['word'] for w in words[a:b + 1]]
    after = [w['word'] for w in words[b + 1:hi + 1]]
    if tag['type'] == 'phantom_word' and all(norm(w) in FILLERS for w in span):
        return {'winner': 'unresolved', 'reason': 'local Whisper drops fillers, so it cannot judge this'}
    heard = tag.get('heard', '')
    gemini_side = (span + [heard]) if tag['type'] == 'missing_word' else ([heard] if heard else [])
    candidates = {'assemblyai': ' '.join(before + span + after),
                  'gemini': ' '.join(before + gemini_side + after)}
    try:
        text = transcriber(audio, max(0.0, words[lo]['start'] - 0.2), words[hi]['end'] + 0.2)
    except Exception as exc:  # a tiebreak failure never stops the map
        return {'winner': 'unresolved', 'reason': f'local Whisper failed: {type(exc).__name__}'}
    if not text:
        return {'winner': 'unresolved', 'reason': 'local Whisper unavailable'}
    # Score only the disputed words: the share of each side's words that Whisper actually heard.
    heard_by_whisper = norm(text).split()

    def support(side_words):
        toks = norm(' '.join(side_words)).split()
        if not toks:  # e.g. Gemini says a word is not there at all
            return None
        pool, hit = list(heard_by_whisper), 0
        for t in toks:
            match = next((h for h in pool if h == t or (len(t) > 3 and difflib.SequenceMatcher(None, h, t).ratio() >= 0.8)), None)
            if match:
                pool.remove(match)
                hit += 1
        return round(hit / len(toks), 3)
    scores = {'assemblyai': support(span), 'gemini': support(gemini_side)}
    if scores['gemini'] is None:  # phantom word: Whisper decides whether the span is there
        scores['gemini'] = round(1 - (scores['assemblyai'] or 0), 3)
    gap = scores['gemini'] - scores['assemblyai']
    winner = 'gemini' if gap >= margin else ('assemblyai' if gap <= -margin else 'unresolved')
    return {'winner': winner, 'scores': scores, 'whisper': text.strip(), 'assemblyai': ' '.join(span),
            'gemini': ' '.join(gemini_side)}

PROMPT = """You are listening to recorded dialogue for a video editor. The numbered transcript
below covers exactly this audio. Listen to all of it and tag only notable delivery:
{types}

Rules: cite word indices from this transcript only (word_first..word_last inclusive, both
from this chunk). Do not tag normal clean speech. Do not invent issues to fill the list.
Do not judge content or editing choices. For misheard, give the exact audible words for
that index range in heard. For missing_word, use the nearest transcript index and give the
audible words in heard. Return JSON exactly:
{{"tags":[{{"word_first":int,"word_last":int,"type":"<type>","confidence":"high|medium|low","heard":"only for misheard/missing_word","note":"short"}}]}}

Transcript (index:word, each line starts at its offset in this audio):
{transcript}
"""


def chunks(words, chunk_s):
    """Split at the longest inter-word pause in the last 20% of each window."""
    out, start = [], 0
    while start < len(words):
        limit = words[start]['start'] + chunk_s
        end = start
        while end + 1 < len(words) and words[end + 1]['end'] <= limit:
            end += 1
        if end + 1 < len(words):
            window = range(max(start, end - max(1, (end - start) // 5)), end)
            end = max(window, key=lambda i: words[i + 1]['start'] - words[i]['end'], default=end)
        out.append((start, end))
        start = end + 1
    return out


def transcript_block(words, first, last, offset):
    lines, line = [], []
    for i in range(first, last + 1):
        if not line:
            line.append(f'@{words[i]["start"] - offset:.1f}s')
        line.append(f'{i}:{words[i]["word"]}')
        if len(line) >= 16 or i == last:
            lines.append(' '.join(line))
            line = []
    return '\n'.join(lines)


def clean_tags(raw, first, last):
    kept, dropped = [], 0
    tags = raw.get('tags') if isinstance(raw, dict) else None
    if not isinstance(tags, list):
        return [], 1
    for tag in tags:
        try:
            a, b = int(tag['word_first']), int(tag['word_last'])
            ok = first <= a <= b <= last and tag['type'] in TYPES and tag.get('confidence') in CONFIDENCE
        except (KeyError, TypeError, ValueError):
            ok = False
        if ok:
            kept.append({'word_first': a, 'word_last': b, 'type': tag['type'],
                         'confidence': tag['confidence'], 'note': str(tag.get('note', ''))[:200],
                         **({'heard': str(tag.get('heard', ''))[:200]} if tag['type'] in DISPUTES else {})})
        else:
            dropped += 1
    return kept, dropped


def build(words_path, audio, out, cache_dir, chunk_s=600, parallel=4, max_usd=15.0,
          max_calls=80, models=gemini_audio.FALLBACK_MODELS, caller=gemini_audio.call_chain, encoder=None,
          transcriber=local_whisper):
    words = json.loads(Path(words_path).read_text())['words']
    encoder = encoder or gemini_audio.encode_mp3
    budget = gemini_audio.Budget(max_usd, max_calls)
    prompt_types = '\n'.join(f'- {k}: {v}' for k, v in TYPES.items())

    def run(span):
        first, last = span
        start = max(0.0, words[first]['start'] - 0.3)
        end = words[last]['end'] + 0.3
        try:
            audio_bytes = encoder(audio, start, end - start)
            prompt = PROMPT.format(types=prompt_types, transcript=transcript_block(words, first, last, start))
            r = caller(audio_bytes, prompt, cache_dir=cache_dir, budget=budget, models=models)
        except Exception as exc:
            r = {'status': 'UNAVAILABLE', 'detail': f'listener chunk failed: {type(exc).__name__}'}
        if r.get('status') in ('OK', 'CACHED') and (not isinstance(r.get('result'), dict) or not isinstance(r['result'].get('tags'), list)):
            r = {**r, 'status': 'UNAVAILABLE', 'detail': 'listener response has an invalid tags array'}
        tags, dropped = clean_tags(r.get('result'), first, last) if r['status'] in ('OK', 'CACHED') else ([], 0)
        return {'word_range': [first, last], 'audio_window_s': [round(start, 3), round(end, 3)],
                'status': r['status'], 'model': r.get('model'), 'detail': r.get('detail'), 'usage': r.get('usage'),
                'usd': round(r.get('usd', 0.0), 4), 'tags': tags, 'invalid_tags_dropped': dropped}

    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
        results = list(pool.map(run, chunks(words, chunk_s)))
    tags = [t for r in results for t in r.pop('tags')]
    for tag in tags:
        if tag['type'] in DISPUTES:
            tag['vote'] = tiebreak(words, audio, tag, transcriber)
    available = sum(1 for r in results if r['status'] != 'UNAVAILABLE')
    doc = {'schema': SCHEMA, 'models': sorted({r['model'] for r in results if r.get('model')}),
           'source': {'words': str(Path(words_path).resolve()),
                      'words_sha256': hashlib.sha256(Path(words_path).read_bytes()).hexdigest(),
                      'audio': str(Path(audio).resolve())},
           'tag_types': TYPES, 'chunks': results, 'tags': sorted(tags, key=lambda t: t['word_first']),
           'coverage': {'chunks': len(results), 'available': available,
                        'status': 'COMPLETE' if available == len(results) else
                                  ('PARTIAL' if available else 'UNAVAILABLE')},
           'cost_estimate_usd': round(sum(r['usd'] for r in results), 4),
           'scope': 'Listener notes keyed to transcript words. Not a selection, not an approval.'}
    out = Path(out)
    with out.open('x') as f:
        json.dump(doc, f, indent=1)
        f.write('\n')
    return doc


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--words', required=True, type=Path, help='AssemblyAI raw_transcript.vN.json')
    ap.add_argument('--audio', required=True, type=Path, help='played dialogue PCM on the same clock')
    ap.add_argument('--out', required=True, type=Path, help='new delivery_map.vN.json (never overwritten)')
    ap.add_argument('--cache-dir', required=True, type=Path)
    ap.add_argument('--chunk-s', type=float, default=600)
    ap.add_argument('--parallel', type=int, default=4)
    ap.add_argument('--max-usd', type=float, default=15.0)
    ap.add_argument('--max-calls', type=int, default=80)
    ap.add_argument('--model', help='one model only; default is the fallback chain ' + ', '.join(gemini_audio.FALLBACK_MODELS))
    a = ap.parse_args()
    if a.out.exists():
        ap.error('output exists; use a new revision path')
    models = (a.model,) if a.model else gemini_audio.FALLBACK_MODELS
    doc = build(a.words, a.audio, a.out, a.cache_dir, a.chunk_s, a.parallel, a.max_usd, a.max_calls, models)
    print(json.dumps({'status': doc['coverage']['status'], 'chunks': doc['coverage'],
                      'tags': len(doc['tags']), 'cost_estimate_usd': doc['cost_estimate_usd'],
                      'unavailable': [c['detail'] for c in doc['chunks'] if c['status'] == 'UNAVAILABLE'][:3]}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
