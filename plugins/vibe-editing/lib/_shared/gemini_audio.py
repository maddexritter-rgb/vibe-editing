"""Bounded Gemini audio calls: cached, budgeted, one retry, never blocking.

Gemini is an extra listener whose notes the editor-model reads. It never grants or
withholds approval. Every failure returns a recorded UNAVAILABLE result so the
calling stage continues on its local evidence instead of stalling or looping.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# Listener-notes chain. 2026-10-15: no model qualified on clipped-ending controls (endings are
# measured by join_check.py instead); 4 Argon gave the richest delivery notes on a test source.
# These notes inform the editor-model; they are never a qualified approval.
DEFAULT_MODEL = 'gemini-4-argon'
FALLBACK_MODELS = ('gemini-4-argon', 'gemini-3.8-flash', 'gemini-3.1-pro-preview')
UNAVAILABLE_MODEL_CODES = (404, 429, 500, 503, 504)
# USD per 1M tokens for prompts <= 200k tokens (paid tier, checked 2026-10-15).
# Used only for the budget cap and cost report; the usage log is the real record.
PRICES = {'gemini-3.1-pro-preview': (2.00, 12.00), 'gemini-4-argon': (2.00, 10.00),
          'gemini-3.8-flash': (0.75, 3.75)}
RETRYABLE = {429, 500, 503, 504}
RETRY_WAIT_S = 20


def credential(slot='paid'):
    import os, sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from vibe_credentials import load_keys
    load_keys()
    key = os.environ.get('GEMINI_API_KEY', '').strip()
    if not key:
        raise ValueError('Connect GEMINI_API_KEY through onboarding')
    return key


def encode_mp3(source, start_s, duration_s):
    """Mono 16 kHz MP3 of a source window (Gemini listens at 16 kHz; keeps requests small)."""
    return subprocess.check_output([
        'ffmpeg', '-v', 'error', '-ss', f'{start_s:.3f}', '-t', f'{duration_s:.3f}', '-i', str(source),
        '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame', '-b:a', '48k', '-f', 'mp3', '-'])


def estimate_usd(model, usage):
    price_in, price_out = PRICES.get(model, PRICES[DEFAULT_MODEL])
    prompt = usage.get('promptTokenCount', 0)
    out = usage.get('candidatesTokenCount', 0) + usage.get('thoughtsTokenCount', 0)
    return (prompt * price_in + out * price_out) / 1_000_000


class Budget:
    """Stops issuing new calls once the cap is reached; calls already made still count."""

    def __init__(self, max_usd=15.0, max_calls=80):
        self.max_usd, self.max_calls = max_usd, max_calls
        self.spent, self.calls = 0.0, 0
        self.lock = threading.Lock()

    def take(self):
        with self.lock:
            if self.calls >= self.max_calls or self.spent >= self.max_usd:
                return False
            self.calls += 1
            return True

    def add(self, usd):
        with self.lock:
            self.spent += usd


def call(audio_bytes, prompt, *, cache_dir, budget, model=DEFAULT_MODEL, slot='paid',
         max_output_tokens=16384, timeout=240, wait=time.sleep):
    """Return {'status': 'OK'|'CACHED'|'UNAVAILABLE', 'result', 'usage', 'usd', 'detail'}.

    The cache key covers audio bytes, prompt and model, so a rerun or redesign never
    pays twice. At most two HTTP attempts; then UNAVAILABLE, never an exception.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(audio_bytes + b'\0' + prompt.encode() + b'\0' + model.encode()).hexdigest()
    cached = cache_dir / f'{key}.json'
    if cached.exists():
        try:
            saved = json.loads(cached.read_text())
            if not isinstance(saved, dict) or not isinstance(saved.get('result'), dict):
                raise ValueError('invalid listener cache schema')
            return {**saved, 'status': 'CACHED', 'usd': 0.0}
        except (OSError, ValueError):
            pass  # An interrupted cache is a miss, never a fatal listener failure.
    if not budget.take():
        return {'status': 'UNAVAILABLE', 'detail': 'budget cap reached; continuing on local evidence'}
    try:
        api_key = credential(slot)
    except (OSError, ValueError) as exc:
        return {'status': 'UNAVAILABLE', 'detail': f'credential: {exc}'}
    body = json.dumps({
        'contents': [{'role': 'user', 'parts': [
            {'inlineData': {'mimeType': 'audio/mpeg', 'data': base64.b64encode(audio_bytes).decode()}},
            {'text': prompt}]}],
        'generationConfig': {'responseMimeType': 'application/json', 'maxOutputTokens': max_output_tokens},
    }).encode()
    url = ('https://generativelanguage.googleapis.com/v1beta/models/'
           + urllib.parse.quote(model, safe='') + ':generateContent')
    detail = ''
    for attempt in (1, 2):
        request = urllib.request.Request(url, data=body, headers={
            'Content-Type': 'application/json', 'x-goog-api-key': api_key})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read())
            break
        except urllib.error.HTTPError as exc:
            message = exc.read().decode(errors='replace').replace(api_key, '[REDACTED]')
            detail = f'HTTP {exc.code}: {message[:300]}'
            if exc.code not in RETRYABLE or attempt == 2:
                return {'status': 'UNAVAILABLE', 'detail': detail, 'http_status': exc.code}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            detail = f'{type(exc).__name__}: {str(exc).replace(api_key, "[REDACTED]")[:300]}'
            if attempt == 2:
                return {'status': 'UNAVAILABLE', 'detail': detail}
        wait(RETRY_WAIT_S)
    usage = payload.get('usageMetadata', {})
    usd = estimate_usd(model, usage)
    budget.add(usd)
    candidates = payload.get('candidates') or []
    if not candidates or candidates[0].get('finishReason') != 'STOP':
        return {'status': 'UNAVAILABLE', 'usage': usage, 'usd': usd,
                'detail': f'incomplete response ({(candidates or [{}])[0].get("finishReason")})'}
    text = ''.join(p.get('text', '') for p in candidates[0].get('content', {}).get('parts', [])
                   if not p.get('thought'))
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return {'status': 'UNAVAILABLE', 'usage': usage, 'usd': usd, 'detail': 'response was not JSON'}
    saved = {'result': result, 'usage': usage, 'model': model,
             'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}
    descriptor, pending = tempfile.mkstemp(dir=cache_dir, suffix='.pending')
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(saved, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, cached)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)
    return {**saved, 'status': 'OK', 'usd': usd}


def call_chain(audio_bytes, prompt, *, models=FALLBACK_MODELS, **kwargs):
    """Try each model in order, moving on only when one is busy or out of quota.

    A model that answers is final: the chain never asks another model for a
    different opinion. Cached answers from any chain model are reused first.
    """
    tried = []
    for model in models:
        result = call(audio_bytes, prompt, model=model, **kwargs)
        tried.append({'model': model, 'status': result['status'], 'detail': result.get('detail')})
        if result['status'] != 'UNAVAILABLE':
            return {**result, 'model': model, 'tried': tried}
        if result.get('http_status') not in UNAVAILABLE_MODEL_CODES:
            break  # budget, credential or response problems are not fixed by another model
    return {**result, 'tried': tried}

