#!/usr/bin/env python3
"""Shared ASR from the live edit pipeline: AssemblyAI editorial ingest; Groq/local audits.
Preserve provider evidence and mark fallback editorial transcripts PARTIAL.
"""
from __future__ import annotations

import os
import json
import hashlib
import fcntl
import re
import subprocess
import tempfile
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from onboard import private_json
from vibe_credentials import load_keys
load_keys()

GROQ_MODEL = "whisper-large-v3"
MLX_MODEL = "mlx-community/whisper-large-v3-mlx"
AAI_BASE = "https://api.assemblyai.com"
AAI_MODELS = ["universal-3-5-pro", "universal-2"]


class AsrUnavailable(RuntimeError):
    """No transcription engine could produce a result. Gates must go UNKNOWN, not PASS."""


class AsrPending(AsrUnavailable):
    """A paid job may still be running. Resume its receipt; never submit a second job."""


def content_digest(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def api_key(name: str, config_path: str | None = None) -> str | None:
    return os.environ.get(name)


def groq_key() -> str | None:
    return api_key("GROQ_API_KEY")


def assemblyai_key() -> str | None:
    return api_key("ASSEMBLYAI_API_KEY")


def assemblyai_keys() -> list[str]:
    key = assemblyai_key()
    return [key] if key else []


_LAST_AAI_ERROR = ""
# Only account-level refusals rotate keys; a retry after a timeout could bill a running job twice.
_AAI_ACCOUNT_ERROR = re.compile(r"balance|top up|401|403|unauthori[sz]ed|invalid api key|authentication", re.I)


def _wav16(clip_path: str) -> str:
    wav = tempfile.mktemp(suffix=".wav")
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", clip_path, "-vn", "-ac", "1", "-ar", "16000", wav],
        capture_output=True,
    )
    if r.returncode != 0 or not os.path.exists(wav):
        raise AsrUnavailable(f"ffmpeg audio extract failed: {r.stderr.decode()[:200]}")
    return wav


def _groq(wav: str, key: str) -> dict | None:
    try:
        import httpx
        with open(wav, "rb") as f:
            resp = httpx.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                # Groq behind Cloudflare 403s non-browser UAs (error 1010)
                headers={"Authorization": f"Bearer {key}",
                         "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
                files={"file": ("audio.wav", f, "audio/wav")},
                data={"model": GROQ_MODEL, "response_format": "verbose_json",
                      "timestamp_granularities[]": "word"},
                timeout=120,
            )
        resp.raise_for_status()
        out = resp.json()
        words = out.get("words") or []
        if not words:
            for s in out.get("segments", []):
                words.extend(s.get("words", []))
        return {"text": out.get("text", ""), "words": words,
                "segments": out.get("segments", []), "engine": "groq"}
    except Exception as e:
        print(f"[asr] groq failed ({type(e).__name__}: {e}) — falling back to local mlx_whisper", flush=True)
        return None


def _assemblyai(wav: str, key: str, prompt: str | None,
                keyterms: list[str] | None, state_path=None) -> dict | None:
    """Submit once, persist the accepted ID immediately, and resume exact-ID polling."""
    global _LAST_AAI_ERROR
    import httpx
    options = {"speech_models": AAI_MODELS, "language_code": "en",
               "disfluencies": True, "speaker_labels": True}
    if prompt:
        options["prompt"] = prompt
    if keyterms:
        options["keyterms_prompt"] = [str(x) for x in keyterms if str(x).strip()][:900]
    scope = {"audio_sha256": content_digest(wav), "options": options,
             "key_fingerprint": hashlib.sha256(key.encode()).hexdigest()}
    if state_path is None:
        digest = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
        state_path = Path(os.environ.get("VIBE_ASR_CACHE", str(Path.home() / ".cache/vibe-editing/asr"))) / 'jobs' / (digest + '.json')
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    # Prevent two processes from submitting the same source concurrently.
    descriptor = os.open(str(state_path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AsrPending('Another process owns this ASR job. Resume the same receipt later.') from None
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
                if not isinstance(state, dict):
                    raise ValueError('invalid ASR receipt schema')
            except (OSError, ValueError):
                raise AsrPending('ASR receipt is unreadable; reconcile it before any resubmission.') from None
            if state.get('scope') != scope:
                raise AsrPending('ASR source/settings changed. Use a new versioned output/receipt.')
        else:
            state = {'schema': 'vibe-asr-job/v1', 'scope': scope}
        headers = {"authorization": key}
        transcript_id = state.get('transcript_id')
        if not transcript_id and state.get('phase') == 'submitting':
            raise AsrPending('Submission outcome unknown. Reconcile the saved receipt before resubmitting.')
        if not transcript_id:
            try:
                with open(wav, 'rb') as source:
                    upload = httpx.post(f"{AAI_BASE}/v2/upload", headers=headers, content=source, timeout=180)
                upload.raise_for_status()
                body = {**options, 'audio_url': upload.json()['upload_url']}
                state.update(phase='submitting')
                private_json(state_path, state)  # Write intent before the billable request.
                submitted = httpx.post(f"{AAI_BASE}/v2/transcript", headers=headers, json=body, timeout=60)
                submitted.raise_for_status()
                transcript_id = submitted.json().get('id')
                if not transcript_id:
                    raise AsrPending('Submit returned no ID. Reconcile the saved receipt.')
                state.update(transcript_id=transcript_id, phase='polling')
                private_json(state_path, state)  # Durable before the first GET or process interruption.
            except AsrPending:
                raise
            except Exception as exc:
                if transcript_id:
                    raise AsrPending(f'Accepted job {transcript_id} could not be saved; reconcile that ID before resubmitting.') from None
                code = getattr(getattr(exc, 'response', None), 'status_code', None)
                if state.get('phase') == 'submitting':
                    if code in (400, 401, 402, 403, 422, 429):
                        state['phase'] = 'rejected'
                        private_json(state_path, state)
                    else:
                        raise AsrPending('Submit outcome unknown; resume/reconcile the same receipt.') from None
                _LAST_AAI_ERROR = f'{type(exc).__name__}: HTTP {code or "unavailable"}'
                return None
        deadline = time.time() + 900
        try:
            while time.time() < deadline:
                response = httpx.get(f"{AAI_BASE}/v2/transcript/{transcript_id}", headers=headers, timeout=60)
                response.raise_for_status()
                data = response.json()
                if data.get('status') == 'completed':
                    break
                if data.get('status') == 'error':
                    state['phase'] = 'failed'
                    private_json(state_path, state)
                    _LAST_AAI_ERROR = 'Provider reported a terminal transcription error for the recorded job.'
                    return None
                time.sleep(2)
            else:
                raise AsrPending('Transcription still pending. Resume the same receipt to poll its ID.')
        except AsrPending:
            raise
        except Exception:
            raise AsrPending('Polling interrupted. Resume the same receipt; the accepted job ID is saved.') from None
        state['phase'] = 'completed'
        private_json(state_path, state)

    def word(w):
        return {'word': w.get('text', '').strip(), 'start': w.get('start', 0) / 1000.0,
                'end': w.get('end', 0) / 1000.0, 'confidence': w.get('confidence'),
                'speaker': w.get('speaker')}
    words = [word(w) for w in data.get('words') or []]
    utterances = [{'text': u.get('text', ''), 'start': u.get('start', 0) / 1000.0,
                   'end': u.get('end', 0) / 1000.0, 'speaker': u.get('speaker'),
                   'confidence': u.get('confidence'),
                   'words': [word(w) for w in u.get('words') or []]}
                  for u in data.get('utterances') or []]
    # Older consumers traverse segments[*].words. Preserve every flat word even when
    # the provider omits utterance words (for example an unlabeled or silent turn).
    if words and not any(u['words'] for u in utterances):
        utterances = [{'text': data.get('text', ''), 'start': words[0]['start'],
                       'end': words[-1]['end'], 'words': words}]
    return {'text': data.get('text', ''), 'words': words, 'segments': utterances,
            'utterances': utterances, 'engine': 'assemblyai',
            'model': data.get('speech_model_used') or data.get('speech_model') or 'unknown',
            'evidence_status': 'FULL', 'provider_transcript_id': transcript_id,
            'provider_response': data}


def _mlx(wav: str) -> dict | None:
    try:
        import mlx_whisper
        out = mlx_whisper.transcribe(wav, path_or_hf_repo=MLX_MODEL, word_timestamps=True)
        words, segments = [], []
        for seg in out.get("segments", []):
            segments.append({"text": seg.get("text", ""),
                             "start": seg.get("start"), "end": seg.get("end")})
            for w in seg.get("words", []):
                words.append({"word": w.get("word", "").strip(),
                              "start": w.get("start"), "end": w.get("end")})
        return {"text": out.get("text", ""), "words": words,
                "segments": segments, "engine": "mlx"}
    except Exception as e:
        print(f"[asr] mlx_whisper failed ({type(e).__name__}: {e})", flush=True)
        return None


def _cache_path(clip_path: str, profile: str, prompt=None, keyterms=None) -> Path:
    """Bind cached words to full content, profile, vocabulary and requested models."""
    identity = {'content': content_digest(clip_path), 'profile': profile, 'prompt': prompt,
                'keyterms': keyterms, 'assemblyai_models': AAI_MODELS,
                'groq_model': GROQ_MODEL, 'mlx_model': MLX_MODEL}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    directory = Path(os.environ.get('VIBE_ASR_CACHE', str(Path.home() / '.cache/vibe-editing/asr')))
    directory.mkdir(parents=True, exist_ok=True)
    return directory / (digest + '.json')


def transcribe(clip_path: str, use_cache: bool = True, *, profile: str = "audit",
               prompt: str | None = None, keyterms: list[str] | None = None, state_path=None) -> dict:
    """Transcribe with word timestamps. Raises AsrUnavailable when no engine works.

    CACHED by media content and transcription settings: the step-9 audit fan-out runs audit-audio,
    audit-captions and audit-script on the SAME delivered file, and each used to
    transcribe it independently — 3 identical ASR calls per clip. First caller pays,
    the rest hit the cache. A re-render changes mtime/size, so the cache self-invalidates.
    Set VIBE_ASR_CACHE to relocate; use_cache=False to force a fresh call.
    """
    if profile not in {"audit", "editorial_ingest"}:
        raise ValueError(f"unknown ASR profile: {profile}")
    cp = _cache_path(clip_path, profile, prompt, keyterms) if use_cache else None
    if cp and cp.exists():
        try:
            import json
            res = json.loads(cp.read_text())
            print(f"[asr] cache HIT ({res.get('engine')}, {len(res.get('words', []))} words)", flush=True)
            return res
        except Exception:
            pass                                     # corrupt cache -> re-transcribe
    wav = _wav16(clip_path)
    try:
        res = None
        if profile == "editorial_ingest":
            for n, aai_key in enumerate(assemblyai_keys()):
                global _LAST_AAI_ERROR
                _LAST_AAI_ERROR = ""
                res = _assemblyai(wav, aai_key, prompt, keyterms, state_path=state_path)
                if res is not None or not _AAI_ACCOUNT_ERROR.search(_LAST_AAI_ERROR):
                    break
                print(f"[asr] assemblyai key #{n + 1} refused by account; trying next key", flush=True)
        key = groq_key()
        if res is None and key:
            res = _groq(wav, key)
        if res is None:
            res = _mlx(wav)
        if res is None:
            raise AsrUnavailable(
                "no ASR engine available for profile " + profile
                + ". Gate must report UNKNOWN, not PASS."
            )
        if profile == "editorial_ingest" and res.get("engine") != "assemblyai":
            res["evidence_status"] = "PARTIAL"
            res["limitations"] = [
                "disfluencies may have been normalized away",
                "speaker attribution is not equivalent to AssemblyAI diarization",
            ]
        else:
            res.setdefault("evidence_status", "FULL")
        res["profile"] = profile
        if cp:
            try:
                import json
                private_json(cp, res)
            except Exception:
                pass                                 # cache write is best-effort
        return res
    finally:
        if os.path.exists(wav):
            os.unlink(wav)


if __name__ == "__main__":
    import json
    import sys
    r = transcribe(sys.argv[1])
    print(json.dumps({"engine": r["engine"], "n_words": len(r["words"]),
                      "text": r["text"][:200]}, indent=1))
