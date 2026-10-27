#!/usr/bin/env python3
"""Per-clip transcription with the current AssemblyAI editorial profile.
The legacy filename/CLI is retained for caption and listicle callers.
Groq/local paths remain availability fallbacks when the primary key is absent.
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
import argparse, json, os, re, subprocess, sys, time
from pathlib import Path


def load_zshrc_api_keys() -> None:
    """Lift API_KEY/TOKEN exports from ~/.zshrc into os.environ."""
    zshrc = Path.home() / ".zshrc"
    if not zshrc.exists():
        return
    for line in zshrc.read_text().splitlines():
        m = re.match(r'^\s*export\s+([A-Z_][A-Z0-9_]*)=(.*)$', line)
        if m and ("API_KEY" in m.group(1) or "TOKEN" in m.group(1)):
            val = m.group(2).strip().strip('"').strip("'")
            os.environ.setdefault(m.group(1), val)


def transcribe_clip(source: str, start: float, end: float,
                     api_key: str, model: str = 'whisper-large-v3') -> list[dict]:
    """Extract audio for [start, end] from source, send to Groq, return words."""
    if os.environ.get('ASSEMBLYAI_API_KEY'):
        import tempfile
        root = next(p for p in Path(__file__).resolve().parents if (p / '.claude-plugin').is_dir())
        sys.path.insert(0, str(root / 'lib/_shared'))
        from asr import transcribe, AsrUnavailable
        with tempfile.TemporaryDirectory() as temp:
            audio = Path(temp) / 'clip.wav'
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', str(start), '-i', str(source),
                            '-t', str(end-start), '-vn', '-ac', '1', '-ar', '16000', str(audio)], check=True)
            result = transcribe(str(audio), profile='editorial_ingest')
        if result['engine'] != 'assemblyai':
            raise AsrUnavailable('Caption words fell back from AssemblyAI; retain the full ingest evidence before proceeding.')
        return result['words']
    import requests
    # Unique temp per PROCESS (+ start for multi-chunk within one) so PARALLEL clips never clobber
    # each other's extracted audio. A fixed path keyed only by start (=0 for every clip) caused
    # cross-contaminated captions when clips transcribed concurrently — never again.
    audio = f'/tmp/_transcribe_lv3_{os.getpid()}_{int(start*1000)}.mp3'
    subprocess.run([
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-ss', f'{start:.3f}', '-to', f'{end:.3f}', '-i', source,
        '-vn', '-ac', '1', '-ar', '16000',
        '-c:a', 'libmp3lame', '-b:a', '64k', audio,
    ], check=True)
    with open(audio, 'rb') as f:
        r = requests.post(
            'https://api.groq.com/openai/v1/audio/transcriptions',
            headers={'Authorization': f'Bearer {api_key}'},
            files={'file': (Path(audio).name, f, 'audio/mpeg')},
            data={
                'model': model,
                'response_format': 'verbose_json',
                'timestamp_granularities[]': 'word',
                'language': 'en',
            },
        )
    Path(audio).unlink(missing_ok=True)
    if r.status_code == 429:
        raise RuntimeError(f'rate limited: {r.text[:200]}')
    if r.status_code != 200:
        raise RuntimeError(f'groq error {r.status_code}: {r.text[:300]}')
    j = r.json()
    return [
        {
            'word': w['word'].strip(),
            'start': round(w['start'], 3),
            'end': round(w['end'], 3),
            'prob': 1.0,
        }
        for w in j.get('words', [])
    ]


def _plugin_root() -> Path:
    d = Path(__file__).resolve()
    for p in (d, *d.parents):
        if (p / ".claude-plugin").is_dir():
            return p
    return d.parents[3]


def _transcribe_local_words(source: str, start: float, end: float) -> list[dict]:
    """No-key fallback: drive the local faster-whisper backend for [start,end]."""
    tl = _plugin_root() / "skills" / "long-form-ingest" / "scripts" / "transcribe_local.py"
    if not tl.is_file():
        sys.exit(f"No GROQ_API_KEY and local transcriber missing at {tl}")
    import tempfile as _tf
    tmp = _tf.NamedTemporaryFile(suffix=".json", delete=False).name
    r = subprocess.run([sys.executable, str(tl), str(source), "--out", tmp,
                        "--start", str(start), "--end", str(end)])
    if r.returncode != 0:
        sys.exit("Local transcription failed — install it with: pip install faster-whisper")
    data = json.loads(Path(tmp).read_text())
    try:
        os.unlink(tmp)
    except OSError:
        pass
    return data.get("words", [])


def _local_fallback(args) -> int:
    """Run the whole job locally (no Groq), emitting the SAME schema as the Groq path."""
    print("[transcribe_lv3] no GROQ_API_KEY → using local whisper (key-free).", file=sys.stderr)
    if args.boundaries is None:
        if args.start is None or args.end is None:
            sys.exit('Need either boundaries.json or --start/--end')
        words = _transcribe_local_words(args.source, args.start, args.end)
        args.out.write_text(json.dumps({
            'clip_index': 0, 'source_start': args.start, 'source_end': args.end,
            'duration': round(args.end - args.start, 3), 'words': words,
        }, indent=2))
        print(f'{len(words)} words → {args.out}')
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    b = json.loads(args.boundaries.read_text())
    for clip in b['clips']:
        i, s, e = clip['index'], clip['start'], clip['end']
        words = _transcribe_local_words(args.source, s, e)
        (args.out / f'clip_{i:02d}.json').write_text(json.dumps({
            'clip_index': i, 'source_start': s, 'source_end': e,
            'duration': round(e - s, 3), 'words': words,
        }, indent=2))
        print(f'clip {i:02d}: {len(words)} words (local)')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('source', type=str)
    ap.add_argument('boundaries', type=Path, nargs='?')
    ap.add_argument('--start', type=float)
    ap.add_argument('--end', type=float)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--model', default='whisper-large-v3',
                    choices=['whisper-large-v3', 'whisper-large-v3-turbo'])
    ap.add_argument('--rate-limit-cushion', type=float, default=3.5,
                    help='Seconds between Groq calls (20 RPM = 3s min)')
    args = ap.parse_args()

    # Credentials already loaded privately above; do not scrape shell configuration.
    api_key = os.environ.get('GROQ_API_KEY')
    if not api_key and not os.environ.get('ASSEMBLYAI_API_KEY'):
        # No cloud key → key-free local whisper fallback (same output schema).
        return _local_fallback(args)

    # Single-clip mode
    if args.boundaries is None:
        if args.start is None or args.end is None:
            sys.exit('Need either boundaries.json or --start/--end')
        words = transcribe_clip(args.source, args.start, args.end,
                                api_key, args.model)
        out = {
            'clip_index': 0, 'source_start': args.start, 'source_end': args.end,
            'duration': round(args.end - args.start, 3), 'words': words,
        }
        args.out.write_text(json.dumps(out, indent=2))
        print(f'{len(words)} words → {args.out}')
        return 0

    # Batch mode
    args.out.mkdir(parents=True, exist_ok=True)
    b = json.loads(args.boundaries.read_text())
    last_call = 0.0
    for clip in b['clips']:
        i, s, e = clip['index'], clip['start'], clip['end']
        out_path = args.out / f'clip_{i:02d}.json'
        # Skip if already transcribed with the same boundary (resume support)
        if out_path.exists():
            try:
                existing = json.loads(out_path.read_text())
                if abs(existing.get('source_start', -1) - s) < 0.01 and \
                   abs(existing.get('source_end', -1) - e) < 0.01:
                    print(f'clip {i:02d}: already transcribed → skip')
                    continue
            except Exception:
                pass  # corrupt or stale — re-do
        # Rate-limit guard (Groq free tier = 20 RPM)
        wait = args.rate_limit_cushion - (time.time() - last_call)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(5):
            try:
                words = transcribe_clip(args.source, s, e, api_key, args.model)
                break
            except RuntimeError as ex:
                if 'rate limited' in str(ex) and attempt < 4:
                    print(f'  clip {i:02d} rate-limited, sleep 8s…')
                    time.sleep(8); continue
                raise
        last_call = time.time()
        out_path = args.out / f'clip_{i:02d}.json'
        out_path.write_text(json.dumps({
            'clip_index': i, 'source_start': s, 'source_end': e,
            'duration': round(e - s, 3), 'words': words,
        }, indent=2))
        preview = ' '.join(w['word'] for w in words[:8])
        print(f'clip {i:02d}: {len(words)} words | {preview}…')
    return 0


if __name__ == '__main__':
    sys.exit(main())
